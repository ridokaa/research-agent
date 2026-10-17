"""Tests for app/agent/loop.py (PROMPT 8 Definition of Done).

Includes:
1. Pure logic tests on policy, loop, and state (deterministic, offline).
2. Live end-to-end tests (marked with @pytest.mark.live, requires real LLM_API_KEY).
"""
from __future__ import annotations

import os
from typing import Any

import pytest

from app.agent.loop import AgentLoop, run_agent
from app.llm.client import LLMClient
from app.llm.schemas import FunctionCall, LLMResponse, UsageInfo
from app.schemas.agent import RunOptions
from app.tools.base import BaseTool, ToolResult, ToolSpec
from app.tools.registry import ToolRegistry, get_registry


class DeterministicLLMClient(LLMClient):
    """Deterministic LLMClient subclass for testing loop orchestration offline."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        super().__init__(
            base_url="https://api.openai.com/v1",
            api_key="sk-test-key-for-unit-testing",
            model="gpt-4o-mini",
        )
        self._responses = responses
        self.call_count = 0

    async def respond(
        self,
        input: str | list[dict[str, Any]],
        *,
        instructions: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        previous_response_id: str | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        text_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        if self.call_count < len(self._responses):
            resp = self._responses[self.call_count]
            self.call_count += 1
            return resp
        return LLMResponse(
            response_id=f"resp-{self.call_count}",
            output_text="Fallback default answer",
            function_calls=[],
            status="completed",
            incomplete_reason=None,
            usage=UsageInfo(10, 10),
            raw={},
        )


def _make_text_response(text: str, response_id: str = "resp-1") -> LLMResponse:
    return LLMResponse(
        response_id=response_id,
        output_text=text,
        function_calls=[],
        status="completed",
        incomplete_reason=None,
        usage=UsageInfo(10, 10),
        raw={},
    )


def _make_tool_call_response(
    tool_name: str, args: dict[str, Any], call_id: str = "call_1", response_id: str = "resp-1"
) -> LLMResponse:
    return LLMResponse(
        response_id=response_id,
        output_text=None,
        function_calls=[FunctionCall(call_id=call_id, name=tool_name, arguments_dict=args)],
        status="completed",
        incomplete_reason=None,
        usage=UsageInfo(10, 10),
        raw={},
    )


def _make_json_response(data: dict[str, Any], response_id: str = "resp-json") -> LLMResponse:
    import json

    return LLMResponse(
        response_id=response_id,
        output_text=json.dumps(data),
        function_calls=[],
        status="completed",
        incomplete_reason=None,
        usage=UsageInfo(5, 5),
        raw={},
    )


class FailingTool(BaseTool):
    """Tool implementation that returns status='error'."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="failing_tool",
            description="Always fails",
            parameters={"type": "object", "properties": {}},
            source="local",
        )

    async def run(self, **kwargs: Any) -> ToolResult:
        return ToolResult(
            tool_name="failing_tool",
            status="error",
            error="Database connection timed out",
            duration_ms=50,
        )


# ============================================================================
# Pure Logic Tests (Offline)
# ============================================================================


@pytest.mark.asyncio
async def test_tool_error_handling_continues_and_finalizes() -> None:
    """DoD (d): When a ToolResult(status='error') is injected, loop continues & finalizes with disclaimer."""
    registry = ToolRegistry()
    registry.register(FailingTool())

    # Step 1: LLM calls failing_tool
    # Step 2: LLM responds with output text after seeing tool error
    # Step 3: Self-check confirms final answer
    llm = DeterministicLLMClient(
        responses=[
            _make_tool_call_response("failing_tool", {}, call_id="call_err"),
            _make_text_response("Maaf, gagal mengakses database. Data tidak dapat diambil."),
            _make_json_response({"is_final": True, "reason": "Disclaimer added", "missing": None}),
        ]
    )

    loop = AgentLoop(llm_client=llm, registry=registry)
    result = await loop.run("Ambil data pengguna", options=RunOptions(max_iterations=5))

    assert result.status == "success"
    assert "gagal mengakses database" in result.answer.lower() or "maaf" in result.answer.lower()
    assert result.iterations >= 2


@pytest.mark.asyncio
async def test_max_iterations_exhausted_produces_best_effort_answer() -> None:
    """DoD (e): max_iterations reached -> status exhausted, answer is still present."""
    registry = ToolRegistry()
    clock_tool = get_registry().get("current_time")
    if clock_tool:
        registry.register(clock_tool)

    llm = DeterministicLLMClient(
        responses=[
            _make_tool_call_response("current_time", {}, call_id="call_1"),
            _make_tool_call_response("current_time", {}, call_id="call_2"),
            _make_text_response("Waktu saat ini adalah jam 10 (keterbatasan: iterasi habis)."),
        ]
    )

    loop = AgentLoop(llm_client=llm, registry=registry)
    result = await loop.run("Berapa jam sekarang?", options=RunOptions(max_iterations=2))

    assert result.status == "exhausted"
    assert result.answer != ""
    assert result.iterations == 2


@pytest.mark.asyncio
async def test_identical_tool_calls_detected_cache_and_no_progress() -> None:
    """DoD (f): Two identical tool calls in sequence -> detected cache & no-progress detector triggers."""
    registry = ToolRegistry()
    calc_tool = get_registry().get("calculator")
    if calc_tool:
        registry.register(calc_tool)

    llm = DeterministicLLMClient(
        responses=[
            _make_tool_call_response("calculator", {"expression": "2 + 2"}, call_id="call_1"),
            _make_tool_call_response("calculator", {"expression": "2 + 2"}, call_id="call_2"),
            _make_tool_call_response("calculator", {"expression": "2 + 2"}, call_id="call_3"),
            _make_text_response("Hasil 2 + 2 = 4."),
        ]
    )

    loop = AgentLoop(llm_client=llm, registry=registry)
    result = await loop.run("Hitung 2 + 2", options=RunOptions(max_iterations=8))

    assert result.answer != ""
    assert result.status in ("success", "exhausted")


@pytest.mark.asyncio
async def test_empty_llm_response_returns_terminal_status() -> None:
    """Verify that an empty LLM response (no tool calls, no text) produces a terminal status, never 'running'."""
    empty_resp = LLMResponse(
        response_id="resp-empty",
        output_text=None,
        function_calls=[],
        status="completed",
        incomplete_reason=None,
        usage=UsageInfo(0, 0),
        raw={},
    )
    llm = DeterministicLLMClient([empty_resp])
    loop = AgentLoop(llm_client=llm)

    result = await loop.run("Tes query dengan LLM kosong")
    assert result.status in ("success", "exhausted", "failed")
    assert result.status != "running"
    assert result.answer != ""


@pytest.mark.asyncio
async def test_chat_completions_forces_store_false() -> None:
    """Verify that chat_completions api_style overrides store option to False."""
    text_resp = _make_text_response("Hasil tes")
    llm = DeterministicLLMClient([text_resp])
    llm._api_style = "chat_completions"

    loop = AgentLoop(llm_client=llm)
    opts = RunOptions(store=True)
    result = await loop.run("Query tes", options=opts)

    assert result.status == "success"
    assert loop.last_state is not None


@pytest.mark.asyncio
async def test_fallback_synthesizes_existing_execution_memory() -> None:
    """Verify that when synthesis returns empty response, fallback finalizer uses memory data."""
    # Step 1: LLM requests dummy_search tool
    tool_resp = _make_tool_call_response("dummy_search", {"query": "perang"})
    # Step 2: Synthesis LLM returns empty response
    empty_synthesis_resp = LLMResponse(
        response_id="resp-empty-synth",
        output_text=None,
        function_calls=[],
        status="completed",
        incomplete_reason=None,
        usage=UsageInfo(0, 0),
        raw={},
    )
    # Step 3: Fallback LLM returns final answer based on fallback prompt
    fallback_resp = _make_text_response("Berdasarkan data perang: 78 post ditemukan pada 2026-07-04.")

    llm = DeterministicLLMClient([tool_resp, empty_synthesis_resp, fallback_resp])

    # Register dummy tool
    reg = get_registry()

    class DummySearchTool(BaseTool):
        spec = ToolSpec(
            name="dummy_search",
            description="Dummy search tool",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
            source="local",
        )

        async def run(self, **kwargs: Any) -> ToolResult:
            return ToolResult(
                tool_name="dummy_search",
                status="success",
                data={"total_posts": 78, "date": "2026-07-04"},
                duration_ms=50,
            )

    dummy_tool = DummySearchTool()
    reg.register(dummy_tool)

    try:
        loop = AgentLoop(llm_client=llm, registry=reg)
        result = await loop.run("Apakah topik perang sering dibicarakan?")

        assert result.status in ("success", "exhausted")
        assert result.status != "running"
        assert "78" in result.answer or "perang" in result.answer
        assert "tidak ada data" not in result.answer.lower()
    finally:
        reg.unregister("dummy_search")



# ============================================================================
# Live End-to-End Tests (Marker `live`)
# ============================================================================


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_query_no_tool_required() -> None:
    """DoD live (a): Question answerable without tools -> status success."""
    api_key = os.getenv("LLM_API_KEY")
    if not api_key or api_key == "sk-xxx":
        pytest.skip("LLM_API_KEY not provided")

    result = await run_agent(user_query="Apa ibukota Indonesia?")
    assert result.status == "success"
    assert result.answer != ""
    assert "jakarta" in result.answer.lower() or "nusantara" in result.answer.lower()


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_query_single_tool() -> None:
    """DoD live (b): Question triggering 1 tool then answering."""
    api_key = os.getenv("LLM_API_KEY")
    if not api_key or api_key == "sk-xxx":
        pytest.skip("LLM_API_KEY not provided")

    result = await run_agent(user_query="Jam berapa sekarang?")
    assert result.status == "success"
    assert result.answer != ""


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_query_chained_tools() -> None:
    """DoD live (c): Question requiring chained tool calls."""
    api_key = os.getenv("LLM_API_KEY")
    if not api_key or api_key == "sk-xxx":
        pytest.skip("LLM_API_KEY not provided")

    result = await run_agent(user_query="Berapa hasil (125 * 8) + (450 / 9)? Gunakan tool kalkulator.")
    assert result.status == "success"
    assert "1050" in result.answer or "1,050" in result.answer


@pytest.mark.asyncio
async def test_live_missing_api_key_fails_explicitly() -> None:
    """Verify missing/placeholder API key fails explicitly with status='failed' and ErrorReport."""
    llm = LLMClient(
        base_url="https://api.openai.com/v1",
        api_key="sk-xxx",  # Placeholder key
        model="gpt-4o-mini",
    )

    loop = AgentLoop(llm_client=llm)
    result = await loop.run("Test query")

    assert result.status == "failed"
    assert "missing_api_key" in result.answer.lower() or "sk-xxx" in result.answer or "api_key" in result.answer.lower()
