"""MCP Tool → ToolSpec and CallToolResult → ToolResult adapter."""
from __future__ import annotations

import json
import time
from typing import Any

from mcp.types import CallToolResult, Tool

from app.mcp.client import MCPClient
from app.tools.base import BaseTool, ToolResult, ToolSpec


def _normalize_responses_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Non-destructively normalize inputSchema for OpenAI Responses API compatibility."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}

    res_schema = dict(schema)
    if "type" not in res_schema:
        res_schema["type"] = "object"

    if res_schema.get("type") == "object" and "properties" not in res_schema:
        res_schema["properties"] = {}

    return res_schema


SLOW_MCP_TOOLS = {"get_topics", "get_topic_sentiment", "get_sentiment_trend", "get_trending"}


def mcp_tool_to_spec(tool: Tool, server_alias: str, available: bool = True) -> ToolSpec:
    """Convert an MCP Tool schema to internal ToolSpec with prefix '{server_alias}__{tool_name}'."""
    tool_name = f"{server_alias}__{tool.name}"
    description = (tool.description or tool.name).strip()

    # Extract input schema dict
    parameters: dict[str, Any] = {}
    if hasattr(tool, "input_schema") and tool.input_schema:
        if isinstance(tool.input_schema, dict):
            parameters = tool.input_schema
        elif hasattr(tool.input_schema, "model_dump"):
            parameters = tool.input_schema.model_dump()
    elif hasattr(tool, "inputSchema") and tool.inputSchema:
        if isinstance(tool.inputSchema, dict):
            parameters = tool.inputSchema
        elif hasattr(tool.inputSchema, "model_dump"):
            parameters = tool.inputSchema.model_dump()

    parameters = _normalize_responses_schema(parameters)
    timeout_s = 90.0 if tool.name in SLOW_MCP_TOOLS else 60.0

    return ToolSpec(
        name=tool_name,
        description=description,
        parameters=parameters,
        source="mcp",
        cost_credits=0,
        timeout_s=timeout_s,
        tags=["mcp", server_alias],
        available=available,
    )


def mcp_result_to_tool_result(
    result: CallToolResult,
    tool_name: str,
    duration_ms: int,
) -> ToolResult:
    """Convert MCP CallToolResult content blocks into internal ToolResult."""
    text_blocks: list[str] = []
    structured: Any | None = getattr(result, "structured_content", None)
    if structured is None:
        structured = getattr(result, "data", None)

    content_list = getattr(result, "content", []) or []
    for block in content_list:
        if hasattr(block, "text") and block.text:
            text_blocks.append(block.text)
        elif hasattr(block, "value") and block.value:
            text_blocks.append(str(block.value))

    combined_text = "\n".join(text_blocks).strip()

    # Parse JSON if possible
    parsed_data: Any = None
    if structured is not None:
        parsed_data = structured
    elif combined_text:
        try:
            parsed_data = json.loads(combined_text)
        except json.JSONDecodeError:
            parsed_data = combined_text

    is_err = bool(getattr(result, "is_error", getattr(result, "isError", False)))
    if is_err:
        status = "error"
        error_msg = combined_text or "MCP tool call failed"
        data = None
    else:
        if parsed_data is None or parsed_data == "" or parsed_data == [] or parsed_data == {}:
            status = "empty"
        else:
            status = "success"
        error_msg = None
        data = parsed_data

    raw_dump: dict[str, Any] | None = None
    if hasattr(result, "model_dump"):
        try:
            raw_dump = result.model_dump()
        except Exception:  # noqa: BLE001
            raw_dump = None

    return ToolResult(
        tool_name=tool_name,
        status=status,
        data=data,
        error=error_msg,
        duration_ms=duration_ms,
        credits_used=0,
        raw_data=raw_dump,
    )


class MCPTool(BaseTool):
    """BaseTool implementation wrapping an MCP client and tool call."""

    def __init__(
        self,
        client: MCPClient,
        original_tool_name: str,
        spec: ToolSpec,
    ) -> None:
        self._client = client
        self._original_tool_name = original_tool_name
        self._spec = spec

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def run(self, **kwargs: Any) -> ToolResult:
        if not self._spec.available:
            return ToolResult(
                tool_name=self._spec.name,
                status="error",
                error=f"MCP tool '{self._spec.name}' is currently unavailable (server offline)",
                duration_ms=0,
            )

        start_time = time.monotonic()
        try:
            mcp_result = await self._client.call_tool(
                name=self._original_tool_name,
                arguments=kwargs,
                timeout_s=self._spec.timeout_s,
            )
            duration_ms = int((time.monotonic() - start_time) * 1000)
            return mcp_result_to_tool_result(mcp_result, self._spec.name, duration_ms)
        except Exception as exc:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            is_timeout = isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "timed out" in str(exc).lower()
            return ToolResult(
                tool_name=self._spec.name,
                status="timeout" if is_timeout else "error",
                error=str(exc),
                duration_ms=duration_ms,
            )
