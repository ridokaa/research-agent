"""ToolExecutor with timeout, retry, validation, normalization, and logging."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from uuid import UUID

import httpx
import jsonschema
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.repositories.tool_invocation import ToolInvocationRepo
from app.logging.tracer import RunTracer
from app.schemas.errors import ErrorReport
from app.tools.base import ToolResult
from app.tools.registry import ToolRegistry, get_registry

log = structlog.get_logger(__name__)

_TRANSIENT_EXCEPTIONS = (
    httpx.RequestError,
    TimeoutError,
    OSError,
    ConnectionError,
)


def _is_empty_data(data: Any) -> bool:
    if data is None:
        return True
    if isinstance(data, (list, tuple, set, dict, str)) and len(data) == 0:
        return True
    if isinstance(data, dict):
        if data.get("total") == 0 or data.get("count") == 0:
            return True
        items = data.get("items")
        if items is None:
            items = data.get("data")
        if isinstance(items, (list, tuple)) and len(items) == 0:
            return True
    return False


def _truncate_result(res: ToolResult, max_chars: int = 16000) -> ToolResult:
    if res.data is None:
        return res

    try:
        raw_str = json.dumps(res.data, ensure_ascii=False, default=str)
    except Exception:
        raw_str = str(res.data).encode("utf-8", errors="replace").decode("utf-8", errors="replace")

    if len(raw_str) <= max_chars:
        return res

    res.truncated = True

    if isinstance(res.data, list):
        total = len(res.data)
        truncated_list = []
        for item in res.data:
            truncated_list.append(item)
            if len(json.dumps(truncated_list, default=str)) >= max_chars - 100:
                break
        n_items = len(truncated_list)
        truncated_list.append(f"[dipotong, {n_items} item dari {total}]")
        res.data = truncated_list
    elif isinstance(res.data, dict):
        list_key = None
        for k, v in res.data.items():
            if isinstance(v, list):
                list_key = k
                break
        if list_key and isinstance(res.data[list_key], list):
            orig_list = res.data[list_key]
            total = len(orig_list)
            truncated_list = []
            for item in orig_list:
                truncated_list.append(item)
                if len(json.dumps(truncated_list, default=str)) >= max_chars - 200:
                    break
            n_items = len(truncated_list)
            truncated_list.append(f"[dipotong, {n_items} item dari {total}]")
            res.data[list_key] = truncated_list

        if len(json.dumps(res.data, default=str)) > max_chars:
            s = str(res.data)[:max_chars]
            res.data = f"{s}\n[dipotong]"
    elif isinstance(res.data, str):
        s = res.data[:max_chars]
        res.data = f"{s}\n[dipotong, 1 item dari 1]"

    return res


class ToolExecutor:
    """Executor for executing agent tools safely."""

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        db_session: AsyncSession | None = None,
    ) -> None:
        self._registry = registry if registry is not None else get_registry()
        self._db_session = db_session

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any] | str | None,
        tracer: RunTracer | None = None,
        run_id: UUID | None = None,
        step_seq: int | None = None,
        db_session: AsyncSession | None = None,
    ) -> ToolResult:
        """Execute a tool by name with argument validation, timeout, and retries.

        Returns a ToolResult. Validation errors return status='error' without raising.
        """
        start_time = time.perf_counter()
        tool = self._registry.get(name)

        if tool is None:
            res = ToolResult(
                tool_name=name,
                status="error",
                error=f"Tool '{name}' not found or disabled.",
                duration_ms=0,
            )
            await self._record_execution(
                res=res,
                arguments=arguments if isinstance(arguments, dict) else {},
                tracer=tracer,
                run_id=run_id,
                step_seq=step_seq,
                session=db_session or self._db_session,
            )
            return res

        # Parse string arguments if necessary
        if isinstance(arguments, str):
            try:
                args_dict = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError as err:
                elapsed = int((time.perf_counter() - start_time) * 1000)
                res = ToolResult(
                    tool_name=name,
                    status="error",
                    error=f"Invalid JSON string in arguments for tool '{name}': {err}",
                    duration_ms=elapsed,
                )
                await self._record_execution(
                    res=res,
                    arguments={"raw_arguments": arguments},
                    tracer=tracer,
                    run_id=run_id,
                    step_seq=step_seq,
                    session=db_session or self._db_session,
                )
                return res
        else:
            args_dict = arguments or {}

        # Validate arguments against tool spec JSON Schema
        try:
            jsonschema.validate(instance=args_dict, schema=tool.spec.parameters)
        except jsonschema.ValidationError as ve:
            elapsed = int((time.perf_counter() - start_time) * 1000)
            if tool.spec.source == "verosight":
                report = ErrorReport(
                    component="verosight",
                    stage="validation",
                    message=f"Invalid arguments for tool '{name}': {ve.message}",
                    retryable=False,
                )
                err_msg = report.model_dump_json()
            else:
                err_msg = (
                    f"Invalid arguments for tool '{name}': {ve.message}. "
                    f"Expected schema: {tool.spec.parameters}"
                )
            res = ToolResult(
                tool_name=name,
                status="error",
                error=err_msg,
                duration_ms=elapsed,
            )
            await self._record_execution(
                res=res,
                arguments=args_dict,
                tracer=tracer,
                run_id=run_id,
                step_seq=step_seq,
                session=db_session or self._db_session,
            )
            return res

        # Execute tool with timeout & retries for transient errors
        max_attempts = 3
        attempt = 0
        raw_output: Any = None
        tool_result_obj: ToolResult | None = None

        while attempt < max_attempts:
            attempt += 1
            try:
                task = asyncio.ensure_future(tool.run(**args_dict))
                done, _pending = await asyncio.wait({task}, timeout=tool.spec.timeout_s)
                if task in done:
                    raw_output = task.result()
                    break
                else:
                    task.cancel()
                    try:
                        await task
                    except BaseException:
                        pass
                    elapsed = int((time.perf_counter() - start_time) * 1000)
                    tool_result_obj = ToolResult(
                        tool_name=name,
                        status="timeout",
                        error=f"Tool '{name}' timed out after {tool.spec.timeout_s}s",
                        duration_ms=elapsed,
                    )
                    break
            except TimeoutError:
                elapsed = int((time.perf_counter() - start_time) * 1000)
                tool_result_obj = ToolResult(
                    tool_name=name,
                    status="timeout",
                    error=f"Tool '{name}' timed out after {tool.spec.timeout_s}s",
                    duration_ms=elapsed,
                )
                break
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current is not None and hasattr(current, "uncancel"):
                    try:
                        current.uncancel()
                    except Exception:
                        pass
                elapsed = int((time.perf_counter() - start_time) * 1000)
                tool_result_obj = ToolResult(
                    tool_name=name,
                    status="timeout",
                    error=(
                        f"Tool '{name}' timed out after {tool.spec.timeout_s}s "
                        "(call cancelled at the MCP/anyio boundary)."
                    ),
                    duration_ms=elapsed,
                )
                break
            except _TRANSIENT_EXCEPTIONS as exc:
                if attempt >= max_attempts:
                    elapsed = int((time.perf_counter() - start_time) * 1000)
                    tool_result_obj = ToolResult(
                        tool_name=name,
                        status="error",
                        error=f"Tool '{name}' failed after {max_attempts} attempts: {exc}",
                        duration_ms=elapsed,
                    )
                    break
                await asyncio.sleep(0.1)
            except Exception as exc:
                elapsed = int((time.perf_counter() - start_time) * 1000)
                tool_result_obj = ToolResult(
                    tool_name=name,
                    status="error",
                    error=str(exc),
                    duration_ms=elapsed,
                )
                break

        elapsed = int((time.perf_counter() - start_time) * 1000)

        if tool_result_obj is not None:
            res = tool_result_obj
        elif isinstance(raw_output, ToolResult):
            res = raw_output
            res.duration_ms = elapsed
        else:
            res = ToolResult(
                tool_name=name,
                status="success",
                data=raw_output,
                duration_ms=elapsed,
                credits_used=tool.spec.cost_credits,
            )

        # Normalize empty output
        if res.status == "success" and _is_empty_data(res.data):
            res.status = "empty"

        try:
            from app.observability.metrics import TOOL_CALLS_TOTAL

            TOOL_CALLS_TOTAL.labels(tool=name, status=res.status).inc()
        except Exception:
            pass

        # Truncate large payloads before LLM context entry unless passthrough is enabled
        settings = get_settings()
        if not settings.TOOL_RESULT_PASSTHROUGH:
            res = _truncate_result(res)

        # Log & record invocation
        await self._record_execution(
            res=res,
            arguments=args_dict,
            tracer=tracer,
            run_id=run_id,
            step_seq=step_seq,
            session=db_session or self._db_session,
            source=tool.spec.source,
        )

        return res

    async def execute_parallel(
        self,
        calls: list[tuple[str, dict[str, Any] | str]],
        tracer: RunTracer | None = None,
        run_id: UUID | None = None,
        step_seq: int | None = None,
        db_session: AsyncSession | None = None,
    ) -> list[ToolResult]:
        """Execute multiple tool calls sequentially or in parallel."""
        settings = get_settings()
        if settings.AGENT_PARALLEL_TOOLS and len(calls) > 1:
            tasks = [
                self.execute(
                    name=name,
                    arguments=args,
                    tracer=tracer,
                    run_id=run_id,
                    step_seq=step_seq,
                    db_session=None,
                )
                for name, args in calls
            ]
            raw_results = list(await asyncio.gather(*tasks, return_exceptions=True))
            results: list[ToolResult] = []
            for (name, _), res in zip(calls, raw_results):
                if isinstance(res, ToolResult):
                    results.append(res)
                else:
                    err_msg = str(res) if res is not None else "Unknown error in parallel tool execution"
                    results.append(
                        ToolResult(
                            tool_name=name,
                            status="error",
                            error=f"Tool '{name}' execution failed: {err_msg}",
                            duration_ms=0,
                        )
                    )
            if db_session is not None:
                for (name, args), res in zip(calls, results):
                    args_dict = (
                        args
                        if isinstance(args, dict)
                        else (json.loads(args) if isinstance(args, str) and args.strip().startswith("{") else {})
                    )
                    tool = self._registry.get(name)
                    source = tool.spec.source if tool else "local"
                    await self._record_execution(
                        res=res,
                        arguments=args_dict,
                        tracer=None,
                        run_id=run_id,
                        step_seq=step_seq,
                        session=db_session,
                        source=source,
                    )
            return results

        results: list[ToolResult] = []
        for name, args in calls:
            res = await self.execute(
                name=name,
                arguments=args,
                tracer=tracer,
                run_id=run_id,
                step_seq=step_seq,
                db_session=db_session,
            )
            results.append(res)
        return results

    async def _record_execution(
        self,
        res: ToolResult,
        arguments: dict[str, Any],
        tracer: RunTracer | None,
        run_id: UUID | None,
        step_seq: int | None,
        session: AsyncSession | None,
        source: str = "local",
    ) -> None:
        if tracer is not None:
            tracer.event(
                type="tool_result",
                summary=f"Executed tool '{res.tool_name}' -> {res.status}",
                payload={
                    "tool_name": res.tool_name,
                    "arguments": arguments,
                    "status": res.status,
                    "duration_ms": res.duration_ms,
                    "credits_used": res.credits_used,
                    "error": res.error,
                    "truncated": res.truncated,
                },
                duration_ms=res.duration_ms,
            )

        effective_run_id = run_id or (tracer.run_id if tracer is not None else None)
        if session is not None and effective_run_id is not None:
            try:
                from app.agent.state import _to_jsonable

                repo = ToolInvocationRepo(session)
                raw = res.raw_data if res.raw_data is not None else res.model_dump()
                result_json = _to_jsonable(raw)
                result_payload = (
                    result_json if isinstance(result_json, dict) else {"result": result_json}
                )
                await repo.record_invocation(
                    run_id=effective_run_id,
                    tool_name=res.tool_name,
                    source=source,
                    step_seq=step_seq,
                    arguments=_to_jsonable(arguments),
                    result=result_payload,
                    status=res.status,
                    error=res.error,
                    duration_ms=res.duration_ms,
                    credits_used=res.credits_used,
                )
            except Exception as exc:
                log.warning("tool_executor.db_record_failed", error=str(exc))
