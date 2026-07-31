"""AgentLoop — core agentic loop orchestration.

PROMPT 8 implementation.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.planner import Planner
from app.agent.policy import evaluate_stop_condition
from app.agent.state import (
    AgentState,
    ConversationHistory,
    Message,
    persist_execution_memory_snapshot,
)
from app.config import get_settings
from app.db.repositories.conversation import ConversationRepo
from app.db.repositories.run import RunRepo
from app.db.repositories.step import StepRepo
from app.llm.client import LLMClient, LLMError
from app.llm.schemas import to_function_call_output
from app.logging.tracer import RunTracer
from app.schemas.agent import RunOptions, RunResult
from app.schemas.errors import ErrorReport
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry, get_registry

log = structlog.get_logger(__name__)


class AgentLoop:
    """Core orchestrator for the agentic loop execution."""

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        registry: ToolRegistry | None = None,
        executor: ToolExecutor | None = None,
        planner: Planner | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.registry = registry
        self.executor = executor
        self.planner = planner or Planner()
        self.session = session
        self.last_state: AgentState | None = None
        self.last_tracer: RunTracer | None = None

    async def run(
        self,
        user_query: str,
        conversation_id: UUID | None = None,
        options: RunOptions | None = None,
    ) -> RunResult:
        """Run the agent loop for a user query.

        Signature
        ---------
        async def run(user_query: str, conversation_id: UUID | None, options: RunOptions) -> RunResult
        """
        t0 = time.perf_counter()
        settings = get_settings()

        opts = options or RunOptions(
            max_iterations=settings.AGENT_MAX_ITERATIONS,
            credits_budget=settings.AGENT_CREDITS_BUDGET,
            timeout_s=float(settings.AGENT_RUN_TIMEOUT_S),
            parallel_tools=settings.AGENT_PARALLEL_TOOLS,
            store=settings.LLM_STORE,
        )

        run_id = uuid.uuid4()
        conv_id = conversation_id or uuid.uuid4()

        registry = self.registry if self.registry is not None else get_registry()
        executor = (
            self.executor
            if self.executor is not None
            else ToolExecutor(registry=registry, db_session=self.session)
        )

        # Setup DB persistence objects if session is present
        run_repo: RunRepo | None = None
        step_repo: StepRepo | None = None
        conv_history: ConversationHistory | None = None

        if self.session is not None:
            run_repo = RunRepo(self.session)
            conv_repo = ConversationRepo(self.session)
            step_repo = StepRepo(self.session)

            # Ensure conversation exists
            existing_conv = await conv_repo.get(conv_id)
            if existing_conv is None:
                await conv_repo.create()
                # If a specific ID was passed, update or create with that ID
                stmt_conv = await conv_repo.get(conv_id)
                if stmt_conv is None:
                    from app.db.models import Conversation
                    new_c = Conversation(id=conv_id)
                    self.session.add(new_c)
                    await self.session.flush()

            # Record run entry in DB
            await run_repo.create(conversation_id=conv_id, user_query=user_query, status="running", run_id=run_id)
            conv_history = ConversationHistory(self.session, conv_id)
            await conv_history.load()

        # Initialize tracer
        tracer = RunTracer(run_id=run_id, conversation_id=conv_id, step_repo=step_repo)
        tracer.event(
            "run_start",
            "Agent run started",
            payload={"user_query": user_query, "options": opts.model_dump()},
        )

        # Initialize LLM Client if not supplied
        if self.llm_client is not None:
            llm = self.llm_client
            if getattr(llm, "_tracer", None) is None:
                llm._tracer = tracer
        else:
            llm = LLMClient(
                base_url=settings.LLM_BASE_URL,
                api_key=settings.LLM_API_KEY,
                model=settings.LLM_MODEL,
                timeout=float(settings.LLM_TIMEOUT_S),
                store=opts.store,
                tracer=tracer,
                reasoning_effort=settings.LLM_REASONING_EFFORT,
                api_style=settings.LLM_API_STYLE,
            )

        state = AgentState(
            run_id=run_id,
            conversation_id=conv_id,
            user_query=user_query,
            max_iterations=opts.max_iterations,
            credits_budget=opts.credits_budget,
            started_at=datetime.now(tz=UTC),
            status="running",
        )

        user_msg = Message(conversation_id=conv_id, role="user", content=user_query)
        state.messages.append(user_msg)
        if conv_history is not None:
            await conv_history.persist_iteration(state.messages, run_id=run_id)

        pending_inputs: list[dict[str, Any]] = []
        no_progress_count: int = 0
        final_answer: str | None = None

        try:
            while True:
                state.iteration += 1
                tracer.set_iteration(state.iteration)

                # --- 5. Stop Condition Check (Policy) ---
                elapsed_s = time.perf_counter() - t0
                stop_status = evaluate_stop_condition(
                    state, elapsed_s, no_progress_count=no_progress_count, options=opts
                )

                if stop_status is not None:
                    if stop_status == "no_progress":
                        tracer.event(
                            "thinking",
                            "No progress detected across 3 iterations; forcing finalization",
                            payload={"no_progress_count": no_progress_count},
                        )
                        state.status = "success"
                    else:
                        state.status = stop_status
                        tracer.event(
                            "thinking",
                            f"Stop condition reached: {stop_status}",
                            payload={"reason": stop_status, "iterations": state.iteration},
                        )
                    break

                # --- 1. Planner build_request ---
                instructions, llm_input = self.planner.build_request(
                    state, pending_inputs=pending_inputs, store=opts.store
                )
                tracer.event(
                    "context_built",
                    f"Built request context for iteration {state.iteration}",
                    payload={
                        "iteration": state.iteration,
                        "has_previous_id": state.last_response_id is not None,
                        "memory_items_count": len(state.execution_memory),
                    },
                )

                # --- 2. LLM respond ---
                try:
                    api_style = getattr(llm, "_api_style", settings.LLM_API_STYLE)
                    tools_def = registry.to_tools(api_style=api_style)
                    if opts.tools_allow is not None:
                        tools_def = [t for t in tools_def if t.get("name") in opts.tools_allow]
                    if opts.tools_deny is not None:
                        tools_def = [t for t in tools_def if t.get("name") not in opts.tools_deny]
                    response = await llm.respond(
                        input=llm_input,
                        instructions=instructions,
                        tools=tools_def if tools_def else None,
                        previous_response_id=state.last_response_id if opts.store else None,
                    )
                except LLMError as err:
                    state.status = "failed"
                    tracer.event(
                        "error",
                        f"LLM execution failed: {err.report.message}",
                        payload=err.report.model_dump(),
                    )
                    latency_ms = int((time.perf_counter() - t0) * 1000)
                    if run_repo is not None:
                        await run_repo.update_status(
                            run_id,
                            status="failed",
                            error=err.report.message,
                            iterations=state.iteration,
                            latency_ms=latency_ms,
                        )
                    await tracer.flush()
                    return RunResult(
                        run_id=run_id,
                        conversation_id=conv_id,
                        answer=f"Run failed: {err.report.message}",
                        status="failed",
                        iterations=state.iteration,
                        latency_ms=latency_ms,
                    )
                except Exception as exc:
                    state.status = "failed"
                    rep = ErrorReport(component="llm", stage="http_request", message=str(exc))
                    tracer.event("error", f"LLM call error: {exc}", payload=rep.model_dump())
                    latency_ms = int((time.perf_counter() - t0) * 1000)
                    if run_repo is not None:
                        await run_repo.update_status(
                            run_id,
                            status="failed",
                            error=str(exc),
                            iterations=state.iteration,
                            latency_ms=latency_ms,
                        )
                    await tracer.flush()
                    return RunResult(
                        run_id=run_id,
                        conversation_id=conv_id,
                        answer=f"Run failed: {exc}",
                        status="failed",
                        iterations=state.iteration,
                        latency_ms=latency_ms,
                    )

                state.last_response_id = response.response_id
                pending_inputs = []

                # --- 3. Handle Tool Function Calls ---
                if response.function_calls:
                    tool_names = [fc.name for fc in response.function_calls]
                    tool_calls_payload = [
                        {"call_id": fc.call_id, "name": fc.name, "arguments": fc.arguments_dict}
                        for fc in response.function_calls
                    ]
                    tracer.event(
                        "tool_selected",
                        f"Selected {len(response.function_calls)} tool(s): {', '.join(tool_names)}",
                        payload={"tools": tool_calls_payload},
                    )

                    new_info_obtained = False
                    uncached_calls: list[tuple[Any, tuple[str, dict[str, Any]]]] = []

                    # Deduplication / caching check
                    for fc in response.function_calls:
                        if state.execution_memory.is_cached(fc.name, fc.arguments_dict):
                            cached_item = state.execution_memory.get_cached(fc.name, fc.arguments_dict)
                            tracer.event(
                                "tool_call_cached",
                                f"Tool '{fc.name}' retrieved from execution memory cache",
                                payload={"tool_name": fc.name, "arguments": fc.arguments_dict},
                            )
                            output_str = json.dumps(
                                {
                                    "status": cached_item.status if cached_item else "success",
                                    "summary": cached_item.summary if cached_item else "",
                                    "value": cached_item.value if cached_item else None,
                                },
                                default=str,
                                ensure_ascii=False,
                            )
                            pending_inputs.append(to_function_call_output(fc.call_id, output_str))
                        else:
                            uncached_calls.append((fc, (fc.name, fc.arguments_dict)))

                    # Execute uncached tools
                    if uncached_calls:
                        calls_to_exec = [call_args for _, call_args in uncached_calls]
                        results = await executor.execute_parallel(
                            calls_to_exec,
                            tracer=tracer,
                            run_id=run_id,
                            db_session=self.session,
                        )

                        for (fc, args_tuple), res in zip(uncached_calls, results):
                            state.add_credits(res.credits_used)
                            mem_item = state.execution_memory.add(
                                res, iteration=state.iteration, arguments=fc.arguments_dict
                            )
                            tracer.event(
                                "memory_write",
                                f"Recorded memory item for '{res.tool_name}'",
                                payload={
                                    "key": mem_item.key,
                                    "status": res.status,
                                    "summary": mem_item.summary,
                                },
                            )

                            output_val = (
                                res.data
                                if res.status == "success"
                                else {"status": res.status, "error": res.error or "Tool error/empty"}
                            )
                            output_str = json.dumps(output_val, default=str, ensure_ascii=False)
                            pending_inputs.append(to_function_call_output(fc.call_id, output_str))

                            if res.status == "success":
                                new_info_obtained = True

                    # Update no progress detector
                    if new_info_obtained:
                        no_progress_count = 0
                    else:
                        no_progress_count += 1

                    # DB persistence per iteration
                    if self.session is not None:
                        if conv_history is not None:
                            await conv_history.persist_iteration(
                                state.messages, run_id=run_id, last_response_id=state.last_response_id
                            )
                        await persist_execution_memory_snapshot(
                            self.session, run_id, state.iteration, state.execution_memory
                        )

                    await tracer.flush()
                    continue

                # --- 4. Handle Final Text Output & Self-Check ---
                if response.output_text and not response.function_calls:
                    draft_answer = response.output_text

                    # Execute self-check using finalize.md
                    mem_block = state.execution_memory.get_context_block(
                        max_tokens=2000, current_iteration=state.iteration
                    )
                    finalize_input = self.planner.build_finalize_input(draft_answer, mem_block)

                    is_final = True
                    reason = ""
                    missing = ""

                    try:
                        self_check_resp = await llm.respond(
                            input=finalize_input,
                            instructions="Kamu adalah validator kualitas jawaban agen. Evaluasi apakah draf jawaban didukung data yang ada. Balas HANYA JSON.",
                            text_format={"type": "json_object"},
                        )
                        if self_check_resp.output_text:
                            eval_data = json.loads(self_check_resp.output_text)
                            is_final = bool(eval_data.get("is_final", True))
                            reason = str(eval_data.get("reason", ""))
                            missing = str(eval_data.get("missing", "") or "")
                    except Exception as err:
                        log.warning("agent_loop.self_check_failed", error=str(err))
                        is_final = True

                    if is_final:
                        final_answer = draft_answer
                        state.status = "success"
                        tracer.event(
                            "final_answer",
                            "Final answer verified and produced",
                            payload={"answer": final_answer, "is_final": True},
                        )
                        break

                    # Self-check returned false -> inject reflection prompt
                    tracer.event(
                        "reflection",
                        f"Self-check reflection: {reason}",
                        payload={"reason": reason, "missing": missing},
                    )
                    reflection_msg = self.planner.build_reflection_input(
                        missing=missing, reason=reason
                    )
                    pending_inputs.append(reflection_msg)
                    no_progress_count += 1

                    if self.session is not None:
                        if conv_history is not None:
                            await conv_history.persist_iteration(
                                state.messages, run_id=run_id, last_response_id=state.last_response_id
                            )
                        await persist_execution_memory_snapshot(
                            self.session, run_id, state.iteration, state.execution_memory
                        )

                    await tracer.flush()
                    continue

                # If model returned neither function calls nor output text, break to finalizer
                break

            # --- Exhausted / Timeout / Fallback Finalization ---
            if final_answer is None:
                mem_block = state.execution_memory.get_context_block(
                    max_tokens=2000, current_iteration=state.iteration
                )
                fallback_prompt = self.planner.build_fallback_prompt(user_query, mem_block)
                try:
                    fallback_resp = await llm.respond(
                        input=fallback_prompt,
                        instructions="Kamu adalah agen riset. Jawab sejujurnya berdasarkan data yang ada dan sebutkan keterbatasannya. JANGAN mengarang data.",
                    )
                    final_answer = (
                        fallback_resp.output_text
                        or "Tidak dapat memperoleh jawaban lengkap dari data tool yang tersedia."
                    )
                except Exception as exc:
                    if mem_block:
                        final_answer = f"Berdasarkan data yang terkumpul: {mem_block[:300]} (keterbatasan: iterasi/budget habis)."
                    else:
                        final_answer = f"Informasi tidak cukup untuk menjawab secara lengkap ({exc})."

                tracer.event(
                    "final_answer",
                    f"Fallback answer generated (status={state.status})",
                    payload={"answer": final_answer, "status": state.status},
                )

        finally:
            if self.llm_client is None:
                await llm.aclose()

        latency_ms = int((time.perf_counter() - t0) * 1000)

        # Record assistant final message
        if final_answer:
            state.messages.append(
                Message(conversation_id=conv_id, role="assistant", content=final_answer)
            )

        tracer.event(
            "run_end",
            f"Run completed with status={state.status}",
            payload={
                "status": state.status,
                "iterations": state.iteration,
                "latency_ms": latency_ms,
                "credits_used": state.credits_used,
            },
        )

        if self.session is not None and run_repo is not None:
            await run_repo.update_status(
                run_id,
                status=state.status,
                final_answer=final_answer or "",
                iterations=state.iteration,
                credits_used=state.credits_used,
                latency_ms=latency_ms,
                last_response_id=state.last_response_id,
            )
            if conv_history is not None:
                await conv_history.persist_iteration(
                    state.messages, run_id=run_id, last_response_id=state.last_response_id
                )

        await tracer.flush()

        self.last_state = state
        self.last_tracer = tracer

        try:
            from app.observability.metrics import CREDITS_USED_TOTAL, ITERATIONS_HISTOGRAM, RUNS_TOTAL

            RUNS_TOTAL.labels(status=state.status).inc()
            ITERATIONS_HISTOGRAM.observe(state.iteration)
            CREDITS_USED_TOTAL.inc(state.credits_used)
        except Exception:
            pass

        return RunResult(
            run_id=run_id,
            conversation_id=conv_id,
            answer=final_answer or "",
            status=state.status,  # type: ignore[arg-type]
            iterations=state.iteration,
            latency_ms=latency_ms,
        )


async def run_agent(
    user_query: str,
    conversation_id: UUID | None = None,
    options: RunOptions | None = None,
    session: AsyncSession | None = None,
) -> RunResult:
    """Convenience entrypoint for running the AgentLoop."""
    loop = AgentLoop(session=session)
    return await loop.run(user_query=user_query, conversation_id=conversation_id, options=options)
