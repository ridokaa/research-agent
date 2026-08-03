"""Tests for CancelledError and asyncio.wait_for fix in ToolExecutor and MCPClient locking."""
from __future__ import annotations

import asyncio
from typing import Any
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.tools.base import BaseTool, ToolResult, ToolSpec
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry
from app.mcp.client import MCPClient


class SlowTestTool(BaseTool):
    """Tool that sleeps longer than its timeout."""

    def __init__(self, delay_s: float = 2.0, timeout_s: float = 0.1) -> None:
        self._spec = ToolSpec(
            name="slow_tool",
            description="Slow tool for testing timeouts",
            parameters={"type": "object", "properties": {}},
            source="local",
            timeout_s=timeout_s,
        )
        self.delay_s = delay_s

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def run(self, **kwargs: Any) -> Any:
        await asyncio.sleep(self.delay_s)
        return "slow output"


class FastTestTool(BaseTool):
    """Tool that succeeds quickly."""

    def __init__(self, name: str = "fast_tool") -> None:
        self._spec = ToolSpec(
            name=name,
            description="Fast tool",
            parameters={"type": "object", "properties": {}},
            source="local",
            timeout_s=5.0,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def run(self, **kwargs: Any) -> Any:
        return f"result of {self.spec.name}"


class CancelledTestTool(BaseTool):
    """Tool that explicitly raises asyncio.CancelledError inside run()."""

    def __init__(self) -> None:
        self._spec = ToolSpec(
            name="cancelled_tool",
            description="Cancelled tool",
            parameters={"type": "object", "properties": {}},
            source="mcp",
            timeout_s=5.0,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def run(self, **kwargs: Any) -> Any:
        raise asyncio.CancelledError("Cancelled via anyio cancel scope")


@pytest.mark.asyncio
async def test_t1_tool_timeout_handling() -> None:
    """T1: Verify tool timeout returns ToolResult(status='timeout') without leaking exception."""
    registry = ToolRegistry()
    slow_tool = SlowTestTool(delay_s=1.0, timeout_s=0.1)
    registry.register(slow_tool)

    executor = ToolExecutor(registry=registry)
    result = await executor.execute("slow_tool", {})

    assert isinstance(result, ToolResult)
    assert result.status == "timeout"
    assert "timed out after 0.1s" in (result.error or "")


@pytest.mark.asyncio
async def test_t2_cancelled_error_handling() -> None:
    """T2: Verify ToolExecutor handles CancelledError from inside tool execution without cancelling executor task."""
    registry = ToolRegistry()
    cancelled_tool = CancelledTestTool()
    registry.register(cancelled_tool)

    executor = ToolExecutor(registry=registry)
    result = await executor.execute("cancelled_tool", {})

    assert isinstance(result, ToolResult)
    assert result.status == "timeout"
    assert "call cancelled at the MCP/anyio boundary" in (result.error or "")


@pytest.mark.asyncio
async def test_t3_execute_parallel_mixed_results(monkeypatch: pytest.MonkeyPatch) -> None:
    """T3: Verify execute_parallel handles mixed timeout and success results cleanly."""
    registry = ToolRegistry()
    slow_tool = SlowTestTool(delay_s=1.0, timeout_s=0.1)
    fast_tool_1 = FastTestTool("fast_tool_1")
    fast_tool_2 = FastTestTool("fast_tool_2")

    registry.register(slow_tool)
    registry.register(fast_tool_1)
    registry.register(fast_tool_2)

    # Enable parallel tools in settings
    from app.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "AGENT_PARALLEL_TOOLS", True)

    executor = ToolExecutor(registry=registry)
    calls = [
        ("slow_tool", {}),
        ("fast_tool_1", {}),
        ("fast_tool_2", {}),
    ]

    results = await executor.execute_parallel(calls)

    assert len(results) == 3
    statuses = [r.status for r in results]
    assert statuses == ["timeout", "success", "success"]


@pytest.mark.asyncio
async def test_t4_mcp_client_lock_serialization() -> None:
    """T4: Verify MCPClient lock serializes calls on the same MCPClient instance."""
    client = MCPClient(alias="test_mcp", transport="sse", url="http://localhost:8080/sse")

    # Mock session & connect
    mock_session = AsyncMock()
    execution_order: list[str] = []

    async def mock_call_tool(name: str, arguments: dict[str, Any]) -> Any:
        execution_order.append(f"start_{name}")
        await asyncio.sleep(0.05)
        execution_order.append(f"end_{name}")
        res = MagicMock()
        res.content = []
        return res

    mock_session.call_tool = AsyncMock(side_effect=mock_call_tool)
    client._session = mock_session
    client._is_connected = True

    # Run two calls concurrently
    t1 = asyncio.create_task(client.call_tool("tool_a", {}))
    t2 = asyncio.create_task(client.call_tool("tool_b", {}))

    await asyncio.gather(t1, t2)

    # Assert sequential execution order due to lock
    assert execution_order in [
        ["start_tool_a", "end_tool_a", "start_tool_b", "end_tool_b"],
        ["start_tool_b", "end_tool_b", "start_tool_a", "end_tool_a"],
    ]
