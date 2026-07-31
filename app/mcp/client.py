"""MCP Client wrapper supporting SSE and stdio transports with auto-reconnect."""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import Any, Literal

import structlog
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import CallToolResult, Tool

logger = structlog.get_logger(__name__)


class MCPClient:
    """Async MCP Client supporting SSE and stdio transports with auto-reconnect."""

    def __init__(
        self,
        alias: str,
        transport: Literal["sse", "stdio"] = "sse",
        url: str | None = None,
        command: str | None = None,
        args: list[str] | None = None,
        headers: dict[str, str] | None = None,
        env: dict[str, str] | None = None,
        timeout_s: float = 30.0,
        max_retries: int = 3,
        backoff_s: float = 1.0,
    ) -> None:
        self.alias = alias
        self.transport = transport
        self.url = url
        self.command = command
        self.args = args or []
        self.headers = headers or {}
        self.env = env
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.backoff_s = backoff_s

        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._is_connected = False

    @property
    def is_connected(self) -> bool:
        return self._is_connected and self._session is not None

    async def connect(self) -> None:
        """Establish connection to the MCP server."""
        if self._is_connected and self._session is not None:
            return

        await self.close()
        self._stack = AsyncExitStack()
        try:
            if self.transport == "sse":
                if not self.url:
                    raise ValueError(f"SSE transport requires 'url' for server '{self.alias}'")
                read_stream, write_stream = await self._stack.enter_async_context(
                    sse_client(url=self.url, headers=self.headers, timeout=self.timeout_s)
                )
            elif self.transport == "stdio":
                if not self.command:
                    raise ValueError(f"Stdio transport requires 'command' for server '{self.alias}'")
                server_params = StdioServerParameters(
                    command=self.command,
                    args=self.args,
                    env=self.env,
                )
                read_stream, write_stream = await self._stack.enter_async_context(
                    stdio_client(server_params)
                )
            else:
                raise ValueError(f"Unsupported MCP transport '{self.transport}'")

            session = await self._stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()
            self._session = session
            self._is_connected = True
            logger.info("mcp.client_connected", alias=self.alias, transport=self.transport)
        except BaseException as exc:
            logger.warning("mcp.client_connect_failed", alias=self.alias, error=str(exc))
            await self.close()
            raise exc

    async def list_tools(self) -> list[Tool]:
        """List available tools from the MCP server with auto-reconnect."""
        last_exc: BaseException | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                if not self.is_connected:
                    await self.connect()
                assert self._session is not None
                res = await self._session.list_tools()
                return res.tools
            except BaseException as exc:
                last_exc = exc
                logger.warning(
                    "mcp.list_tools_error",
                    alias=self.alias,
                    attempt=attempt,
                    max_retries=self.max_retries,
                    error=str(exc),
                )
                await self.close()
                if attempt < self.max_retries:
                    await asyncio.sleep(self.backoff_s * (2 ** (attempt - 1)))

        if last_exc:
            raise last_exc
        return []

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> CallToolResult:
        """Execute a tool call on the MCP server with auto-reconnect."""
        last_exc: BaseException | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                if not self.is_connected:
                    await self.connect()
                assert self._session is not None
                res = await self._session.call_tool(name=name, arguments=arguments or {})
                return res
            except BaseException as exc:
                last_exc = exc
                logger.warning(
                    "mcp.call_tool_error",
                    alias=self.alias,
                    tool_name=name,
                    attempt=attempt,
                    max_retries=self.max_retries,
                    error=str(exc),
                )
                await self.close()
                if attempt < self.max_retries:
                    await asyncio.sleep(self.backoff_s * (2 ** (attempt - 1)))

        if last_exc:
            raise last_exc
        raise RuntimeError(f"Failed to call tool '{name}' on MCP server '{self.alias}'")

    async def close(self) -> None:
        """Close connection and clean up resources."""
        self._is_connected = False
        self._session = None
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception as exc:  # noqa: BLE001
                logger.debug("mcp.client_close_error", alias=self.alias, error=str(exc))
            finally:
                self._stack = None
        logger.info("mcp.client_closed", alias=self.alias)
