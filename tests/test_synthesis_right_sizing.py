"""Unit tests for context right-sizing and synthesis retry (plan-fix-synthesis-right-sizing.md)."""
from __future__ import annotations

import json
from typing import Any

import pytest

from unittest.mock import AsyncMock, MagicMock
from app.agent.loop import AgentLoop
from app.agent.planner import Planner
from app.agent.state import ExecutionMemory, MemoryItem, compact_tool_output_for_llm, prune_noise_from_data
from app.llm.client import LLMClient
from app.llm.schemas import LLMResponse, UsageInfo
from app.schemas.agent import RunOptions
from app.tools.base import BaseTool, ToolResult, ToolSpec
from app.tools.registry import ToolRegistry


class DummyLLMClient(LLMClient):
    """Dummy LLMClient returning prescribed responses."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        super().__init__(
            base_url="https://api.openai.com/v1",
            api_key="sk-test-key",
            model="gpt-4o-mini",
        )
        self.responses = responses
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
        if self.call_count < len(self.responses):
            resp = self.responses[self.call_count]
            self.call_count += 1
            return resp
        return LLMResponse(
            response_id=f"resp-{self.call_count}",
            output_text="Default response",
            function_calls=[],
            status="completed",
            incomplete_reason=None,
            usage=UsageInfo(10, 10),
            raw={},
        )


class HeavyAggregateTool(BaseTool):
    spec = ToolSpec(
        name="verosight__get_topics",
        description="Get topics with heavy posts noise",
        source="local",
        parameters={"type": "object", "properties": {}},
    )

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        by_day_data = [{"date": f"2026-08-{i:02d}", "posts": 100 + i, "likes": 500} for i in range(1, 31)]
        top_posts_noise = [{"id": f"post-{i}", "raw_content": "A" * 2000, "media_urls": ["http://img.com/a.jpg"]} for i in range(50)]
        return {
            "keyword": "nuklir",
            "total_volume": 4500,
            "by_day": by_day_data,
            "topics": [{"name": "Topik A", "description": "Desc A"}],
            "top_posts": top_posts_noise,
        }


def test_compact_tool_output_prunes_noise_and_keeps_by_day():
    by_day_data = [{"date": f"2026-08-{i:02d}", "posts": 10} for i in range(1, 31)]
    raw_data = {
        "keyword": "nuklir",
        "by_day": by_day_data,
        "top_posts": [{"id": 1, "text": "heavy"}],
        "media_urls": ["http://media.com/1.jpg"],
    }
    compacted = compact_tool_output_for_llm(raw_data)
    assert "top_posts" not in compacted
    assert "media_urls" not in compacted
    assert "by_day" in compacted
    assert len(compacted["by_day"]) == 30


def test_prune_noise_from_data_preserves_facts():
    raw_data = {
        "total": 500,
        "by_day": [{"date": "2026-08-01", "val": 10}],
        "top_profiles": [{"user": "alice"}],
    }
    pruned = prune_noise_from_data(raw_data)
    assert "top_profiles" not in pruned
    assert pruned["total"] == 500
    assert len(pruned["by_day"]) == 1


def test_get_context_block_preserves_by_day_without_terpotong():
    mem = ExecutionMemory()
    by_day_data = [{"date": f"2026-08-{i:02d}", "posts": 100 + i} for i in range(1, 31)]
    res = ToolResult(
        tool_name="verosight__get_topics",
        status="success",
        duration_ms=10,
        data={
            "keyword": "nuklir",
            "total_volume": 4500,
            "by_day": by_day_data,
            "top_posts": [{"id": i, "content": "noise"} for i in range(20)],
        },
    )
    mem.add(res, iteration=1)
    block = mem.get_context_block(max_tokens=2000, current_iteration=1, rich=True)

    assert "by_day" in block
    assert "2026-08-01" in block
    assert "2026-08-30" in block
    assert "*(terpotong)*" not in block
    assert "top_posts" not in block


@pytest.mark.asyncio
async def test_synthesis_retry_on_empty_llm_response():
    tool = HeavyAggregateTool()
    registry = ToolRegistry()
    registry.register(tool)
    from app.tools.executor import ToolExecutor
    executor = ToolExecutor(registry=registry)

    from app.llm.schemas import FunctionCall

    # 1. First iteration calls tool
    resp1 = LLMResponse(
        response_id="resp-1",
        output_text=None,
        function_calls=[FunctionCall(call_id="c1", name="verosight__get_topics", arguments_dict={})],
        status="completed",
        incomplete_reason=None,
        usage=UsageInfo(10, 10),
        raw={},
    )
    # 2. Second iteration LLM returns EMPTY response (simulating large context empty output)
    resp2_empty = LLMResponse(
        response_id="resp-2",
        output_text="",
        function_calls=[],
        status="completed",
        incomplete_reason=None,
        usage=UsageInfo(10, 10),
        raw={},
    )
    # 3. Synthesis retry returns text
    resp3_retry = LLMResponse(
        response_id="resp-3",
        output_text="Berdasarkan data topik nuklir, total volume adalah 4500.",
        function_calls=[],
        status="completed",
        incomplete_reason=None,
        usage=UsageInfo(10, 10),
        raw={},
    )
    # 4. Self check response
    resp4_selfcheck = LLMResponse(
        response_id="resp-4",
        output_text=json.dumps({"is_final": True, "reason": "Lengkap"}),
        function_calls=[],
        status="completed",
        incomplete_reason=None,
        usage=UsageInfo(10, 10),
        raw={},
    )

    llm = DummyLLMClient([resp1, resp2_empty, resp3_retry, resp4_selfcheck])
    agent = AgentLoop(llm_client=llm, registry=registry, executor=executor)

    res = await agent.run(user_query="Bahas topik nuklir", options=RunOptions(store=False))

    assert res.status == "success"
    assert "4500" in res.answer
    assert agent.last_state is not None
    assert "top_posts" in agent.last_state.execution_memory.items[0].value


def test_build_finalize_input_includes_tool_availability():
    """Verify build_finalize_input properly structures executed vs available tools for self-check (Langkah 4b)."""
    planner = Planner()
    prompt = planner.build_finalize_input(
        draft_answer="Data sentimen tidak tersedia.",
        execution_memory_text="Volume: 1000",
        executed_tools=["verosight__get_volume"],
        available_tools=["verosight__get_sentiment", "verosight__search_posts"],
    )

    assert "--- Tool yang SUDAH Dipanggil ---" in prompt
    assert "verosight__get_volume" in prompt
    assert "--- Tool TERSEDIA yang BELUM Dipanggil ---" in prompt
    assert "verosight__get_sentiment, verosight__search_posts" in prompt
    assert "Data sentimen tidak tersedia." in prompt


@pytest.mark.asyncio
async def test_proactive_synthesis_right_sizing_triggers_self_check():
    """Verify proactive synthesis right-sizing generates draft text and unlocks self-check."""
    tool = HeavyAggregateTool()
    registry = ToolRegistry()
    registry.register(tool)
    from app.tools.executor import ToolExecutor
    executor = ToolExecutor(registry=registry)
    from app.llm.schemas import FunctionCall

    # 1. Iteration 1: LLM calls tool
    resp1 = LLMResponse(
        response_id="resp-1",
        output_text=None,
        function_calls=[FunctionCall(call_id="c1", name="verosight__get_topics", arguments_dict={})],
        status="completed",
        incomplete_reason=None,
        usage=UsageInfo(10, 10),
        raw={},
    )
    # 2. Iteration 2: Proactive synthesis (tools=None, right-sized input) generates text directly
    resp2_synth = LLMResponse(
        response_id="resp-2",
        output_text="Berdasarkan data topik nuklir, total volume adalah 4500 dengan 30 hari data.",
        function_calls=[],
        status="completed",
        incomplete_reason=None,
        usage=UsageInfo(10, 10),
        raw={},
    )
    # 3. Iteration 2 self-check confirms answer
    resp3_selfcheck = LLMResponse(
        response_id="resp-3",
        output_text=json.dumps({"is_final": True, "reason": "Data lengkap dan valid"}),
        function_calls=[],
        status="completed",
        incomplete_reason=None,
        usage=UsageInfo(10, 10),
        raw={},
    )

    llm = DummyLLMClient([resp1, resp2_synth, resp3_selfcheck])
    agent = AgentLoop(llm_client=llm, registry=registry, executor=executor)

    res = await agent.run(user_query="Bahas topik nuklir", options=RunOptions(store=False))

    assert res.status == "success"
    assert "4500" in res.answer
    assert agent.last_state is not None
    assert agent.last_state.iteration == 2


