"""Tests for MCP client, adapter, manager, VEROSIGHT_MODE filtering, and GET /v1/tools."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from mcp.types import CallToolResult, TextContent, Tool

from app.config import get_settings
from app.main import create_app
from app.mcp.adapter import MCPTool, mcp_result_to_tool_result, mcp_tool_to_spec
from app.mcp.client import MCPClient
from app.mcp.manager import MCPManager, reset_mcp_manager
from app.tools.base import BaseTool, ToolSpec
from app.tools.registry import get_registry, reset_registry


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset global registry and settings cache before each test."""
    get_settings.cache_clear()
    reset_registry()
    reset_mcp_manager()
    yield
    get_settings.cache_clear()
    reset_registry()
    reset_mcp_manager()


class DummyRESTTool(BaseTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="verosight_search_posts",
            description="Search posts on Verosight",
            parameters={"type": "object", "properties": {}},
            source="verosight",
            cost_credits=5,
            available=True,
        )

    async def run(self, **kwargs):
        return None


class DummyLocalTool(BaseTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="calculator",
            description="Evaluate math expressions",
            parameters={"type": "object", "properties": {}},
            source="local",
            cost_credits=0,
            available=True,
        )

    async def run(self, **kwargs):
        return None


# ---------------------------------------------------------------------------
# 1. Adapter tests
# ---------------------------------------------------------------------------

def test_mcp_tool_to_spec_conversion():
    mcp_tool = Tool(
        name="get_sentiment",
        description="Fetch sentiment for topic",
        inputSchema={
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "required": ["topic"],
        },
    )

    spec = mcp_tool_to_spec(mcp_tool, server_alias="verosight", available=True)

    assert spec.name == "verosight__get_sentiment"
    assert spec.description == "Fetch sentiment for topic"
    assert spec.source == "mcp"
    assert spec.available is True
    assert spec.parameters["properties"]["topic"]["type"] == "string"
    assert "mcp" in spec.tags
    assert "verosight" in spec.tags


def test_mcp_result_to_tool_result_success():
    raw_content = json.dumps({"sentiment": "positive", "score": 0.85})
    result = CallToolResult(
        content=[TextContent(type="text", text=raw_content)],
        isError=False,
    )

    tool_res = mcp_result_to_tool_result(result, tool_name="verosight__get_sentiment", duration_ms=120)

    assert tool_res.tool_name == "verosight__get_sentiment"
    assert tool_res.status == "success"
    assert tool_res.data == {"sentiment": "positive", "score": 0.85}
    assert tool_res.error is None
    assert tool_res.duration_ms == 120


def test_mcp_result_to_tool_result_error():
    result = CallToolResult(
        content=[TextContent(type="text", text="API Key Invalid")],
        isError=True,
    )

    tool_res = mcp_result_to_tool_result(result, tool_name="verosight__get_sentiment", duration_ms=50)

    assert tool_res.tool_name == "verosight__get_sentiment"
    assert tool_res.status == "error"
    assert tool_res.error == "API Key Invalid"
    assert tool_res.data is None


@pytest.mark.asyncio
async def test_mcp_tool_unavailable_execution():
    client = MagicMock(spec=MCPClient)
    spec = ToolSpec(
        name="verosight__get_sentiment",
        description="Get sentiment",
        parameters={},
        source="mcp",
        available=False,
    )
    mcp_tool = MCPTool(client=client, original_tool_name="get_sentiment", spec=spec)

    res = await mcp_tool.run(topic="ai")

    assert res.status == "error"
    assert "unavailable" in res.error
    client.call_tool.assert_not_called()


# ---------------------------------------------------------------------------
# 2. Client & Auto-reconnect tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mcp_client_auto_reconnect_retry_success():
    client = MCPClient(
        alias="test_server",
        transport="sse",
        url="https://api.verosight.com/v1/mcp/sse",
        max_retries=3,
        backoff_s=0.01,
    )

    mock_session = AsyncMock()
    mock_session.list_tools.return_value = MagicMock(
        tools=[Tool(name="search", description="search", inputSchema={"type": "object"})]
    )

    with patch.object(client, "connect", new_callable=AsyncMock) as mock_connect:
        def side_effect():
            client._session = mock_session
            client._is_connected = True

        mock_connect.side_effect = side_effect

        tools = await client.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "search"
        assert mock_connect.call_count == 1


# ---------------------------------------------------------------------------
# 3. Manager & Caching tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mcp_manager_startup_graceful_degradation(monkeypatch):
    """If server fails to connect on startup, log warning and register tools as unavailable."""
    monkeypatch.setenv("VEROSIGHT_MODE", "mcp")
    manager = MCPManager()

    with patch.object(MCPClient, "list_tools", side_effect=ConnectionError("Server offline")):
        await manager.connect_all()

    registry = get_registry()
    # verosight tools should be registered as unavailable
    registry_specs = registry.list_specs()
    mcp_specs = [s for s in registry_specs if s.source == "mcp"]

    assert len(mcp_specs) >= 20
    assert all(not s.available for s in mcp_specs)


@pytest.mark.asyncio
async def test_mcp_manager_caching_15_minutes():
    """Tool list should be cached for 15 minutes."""
    manager = MCPManager()
    mock_tool = Tool(name="cached_tool", description="Cached tool desc", inputSchema={"type": "object"})

    with patch.object(MCPClient, "list_tools", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = [mock_tool]

        # First connect
        await manager.connect_all()
        assert mock_list.call_count == 1

        # Second connect immediately after (cache hit)
        await manager.connect_all()
        assert mock_list.call_count == 1  # Should not increase


# ---------------------------------------------------------------------------
# 4. VEROSIGHT_MODE filtering tests
# ---------------------------------------------------------------------------

def test_verosight_mode_filtering(monkeypatch):
    registry = reset_registry()

    rest_tool = DummyRESTTool()
    local_tool = DummyLocalTool()
    registry.register(rest_tool)
    registry.register(local_tool)

    mcp_client = MagicMock(spec=MCPClient)
    mcp_spec = ToolSpec(
        name="verosight__get_sentiment",
        description="Get sentiment analytics",
        parameters={},
        source="mcp",
        available=True,
    )
    mcp_tool = MCPTool(client=mcp_client, original_tool_name="get_sentiment", spec=mcp_spec)
    registry.register(mcp_tool)

    # Mode: rest -> only local + REST
    monkeypatch.setenv("VEROSIGHT_MODE", "rest")
    get_settings.cache_clear()
    rest_specs = registry.list_specs()
    sources = [s.source for s in rest_specs]
    assert "verosight" in sources
    assert "local" in sources
    assert "mcp" not in sources

    # Mode: mcp -> only local + MCP
    monkeypatch.setenv("VEROSIGHT_MODE", "mcp")
    get_settings.cache_clear()
    mcp_specs = registry.list_specs()
    sources_mcp = [s.source for s in mcp_specs]
    assert "mcp" in sources_mcp
    assert "local" in sources_mcp
    assert "verosight" not in sources_mcp

    # Mode: both -> all included, with [REST] and [MCP] prefixes
    monkeypatch.setenv("VEROSIGHT_MODE", "both")
    get_settings.cache_clear()
    both_specs = registry.list_specs()
    sources_both = [s.source for s in both_specs]
    assert "mcp" in sources_both
    assert "verosight" in sources_both
    assert "local" in sources_both

    rest_spec = next(s for s in both_specs if s.source == "verosight")
    mcp_spec_res = next(s for s in both_specs if s.source == "mcp")

    assert rest_spec.description.startswith("[REST]")
    assert mcp_spec_res.description.startswith("[MCP]")


# ---------------------------------------------------------------------------
# 5. GET /v1/tools API Route tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_v1_tools_endpoint(monkeypatch):
    registry = reset_registry()

    local_tool = DummyLocalTool()
    registry.register(local_tool)

    mcp_client = MagicMock(spec=MCPClient)
    mcp_spec = ToolSpec(
        name="verosight__get_sentiment",
        description="Get sentiment analytics",
        parameters={},
        source="mcp",
        cost_credits=0,
        available=True,
    )
    registry.register(MCPTool(client=mcp_client, original_tool_name="get_sentiment", spec=mcp_spec))

    monkeypatch.setenv("VEROSIGHT_MODE", "mcp")
    get_settings.cache_clear()

    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/v1/tools")

    assert response.status_code == 200
    tools_list = response.json()

    assert isinstance(tools_list, list)
    names = [t["name"] for t in tools_list]
    assert "calculator" in names
    assert "verosight__get_sentiment" in names

    mcp_item = next(t for t in tools_list if t["name"] == "verosight__get_sentiment")
    assert mcp_item["source"] == "mcp"
    assert mcp_item["available"] is True
    assert "description" in mcp_item
    assert "cost_credits" in mcp_item
