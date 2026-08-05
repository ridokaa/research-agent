"""Tests for TOOL_RESULT_PASSTHROUGH mode and MCP metadata schema normalization."""
from __future__ import annotations

import json

import pytest

from app.config import get_settings
from app.mcp.adapter import _normalize_responses_schema, mcp_tool_to_spec
from app.tools.base import BaseTool, ToolResult, ToolSpec
from app.tools.executor import ToolExecutor, _truncate_result
from app.tools.registry import ToolRegistry
from app.tools.verosight.helpers import summarize_verosight_response


def test_normalize_responses_schema():
    # Test empty dict
    schema1 = {}
    norm1 = _normalize_responses_schema(schema1)
    assert norm1 == {"type": "object", "properties": {}}

    # Test schema missing type or properties
    schema2 = {"properties": {"query": {"type": "string"}}}
    norm2 = _normalize_responses_schema(schema2)
    assert norm2["type"] == "object"
    assert "query" in norm2["properties"]

    # Test non-dict input
    norm3 = _normalize_responses_schema(None)  # type: ignore
    assert norm3 == {"type": "object", "properties": {}}


def test_summarize_verosight_response_passthrough(monkeypatch):
    data = {
        "total": 100,
        "results": [{"content": f"Post content {i}"} for i in range(50)],
        "sample_posts": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
    }

    # When TOOL_RESULT_PASSTHROUGH is True
    monkeypatch.setattr(get_settings(), "TOOL_RESULT_PASSTHROUGH", True)
    res_passthrough = summarize_verosight_response(data)
    assert len(res_passthrough["results"]) == 50
    assert len(res_passthrough["sample_posts"]) == 10

    # When TOOL_RESULT_PASSTHROUGH is False
    monkeypatch.setattr(get_settings(), "TOOL_RESULT_PASSTHROUGH", False)
    res_summarized = summarize_verosight_response(data)
    assert len(res_summarized["results"]) <= 5

    # Test sample_* list truncation when not using search results envelope
    sample_data = {"sample_posts": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]}
    res_sample = summarize_verosight_response(sample_data)
    assert len(res_sample["sample_posts"]) <= 5


class DummyLargeTool(BaseTool):
    def __init__(self, large_list: list):
        self._large_list = large_list
        self._spec = ToolSpec(
            name="large_tool",
            description="Large payload tool",
            parameters={"type": "object", "properties": {}},
            source="local",
            available=True,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def run(self, **kwargs) -> ToolResult:
        return ToolResult(tool_name="large_tool", status="success", data=self._large_list, duration_ms=10)


@pytest.mark.asyncio
async def test_executor_passthrough(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "TOOL_RESULT_PASSTHROUGH", True)

    large_list = [{"id": i, "data": "x" * 500} for i in range(100)]
    tool = DummyLargeTool(large_list)

    registry = ToolRegistry()
    registry.register(tool)

    executor = ToolExecutor(registry=registry)
    result = await executor.execute("large_tool", {})

    assert result.status == "success", f"Expected success, got {result.status}: {result.error}"
    assert result.truncated is False
    assert len(result.data) == 100
    raw_json = json.dumps(result.data)
    assert "[dipotong" not in raw_json
    assert len(raw_json) > 16000


@pytest.mark.live
@pytest.mark.asyncio
async def test_mcp_live_metadata_ingestion():
    """Live test connecting to Verosight MCP server and verifying metadata ingestion."""
    from app.mcp.client import MCPClient
    from app.mcp.manager import MCPManager

    from app.tools.registry import get_registry

    manager = MCPManager()
    await manager.connect_all()
    registry = get_registry()

    mcp_tools = [spec for spec in registry.list_specs() if spec.source == "mcp"]
    if not mcp_tools:
        pytest.skip("Verosight MCP server unavailable or no tools returned")

    assert len(mcp_tools) > 0
    responses_tools = registry.to_responses_tools()
    assert len(responses_tools) > 0

    for tool in responses_tools:
        assert tool["type"] == "function"
        assert "name" in tool
        assert "description" in tool
        assert "parameters" in tool
        assert tool["parameters"].get("type") == "object"
