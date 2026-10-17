"""MCP Client wrapper built on fastmcp.Client supporting SSE, Streamable HTTP, and stdio transports with auto-reconnect."""
from __future__ import annotations

import asyncio
from typing import Any, Literal

import httpx
import structlog
from fastmcp import Client
from fastmcp.client.transports import (
    SSETransport,
    StdioTransport,
    StreamableHttpTransport,
)
from mcp.types import CallToolResult, Tool

logger = structlog.get_logger(__name__)


class _SessionInitRetryTransport(httpx.AsyncBaseTransport):
    """Custom httpx transport that retries POST requests returning 404 'Session not found' during SSE session init."""

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport,
        max_retries: int = 5,
        backoff_s: float = 0.2,
    ) -> None:
        self._transport = transport
        self.max_retries = max_retries
        self.backoff_s = backoff_s

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._transport.handle_async_request(request)

        # Strictly retry ONLY for 404 with 'Session not found' body during init phase
        if request.method == "POST" and response.status_code == 404:
            body = await response.aread()
            body_str = body.decode("utf-8", errors="ignore").lower()

            if "session not found" in body_str or "not_found" in body_str:
                logger.warning(
                    "mcp.session_not_found_delay_detected",
                    url=str(request.url),
                    msg="Server session propagation pending. Retrying POST...",
                )
                for attempt in range(1, self.max_retries + 1):
                    await asyncio.sleep(self.backoff_s)
                    new_request = httpx.Request(
                        method=request.method,
                        url=request.url,
                        headers=request.headers,
                        content=body if request.content is None else request.content,
                        extensions=request.extensions,
                    )
                    retry_resp = await self._transport.handle_async_request(new_request)
                    if retry_resp.status_code != 404:
                        logger.info(
                            "mcp.session_init_retry_success",
                            attempt=attempt,
                            status=retry_resp.status_code,
                        )
                        return retry_resp

                    retry_body = await retry_resp.aread()
                    retry_body_str = retry_body.decode("utf-8", errors="ignore").lower()
                    if not ("session not found" in retry_body_str or "not_found" in retry_body_str):
                        return retry_resp

                error_msg = f"MCP session init timed out after {self.max_retries} retries (404 Session not found) component=mcp stage=session_init_timeout"
                logger.error(
                    "mcp.session_init_timeout",
                    component="mcp",
                    stage="session_init_timeout",
                    error=error_msg,
                )
                raise RuntimeError(error_msg)

        return response


def _create_mcp_retry_http_client(
    headers: dict[str, str] | None = None,
    auth: Any = None,
    timeout: Any = None,
) -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport()
    retry_transport = _SessionInitRetryTransport(transport)
    return httpx.AsyncClient(headers=headers, auth=auth, timeout=timeout, transport=retry_transport)


class MCPClient:
    """Async MCP Client wrapping fastmcp.Client supporting SSE, Streamable HTTP, and stdio transports."""

    def __init__(
        self,
        alias: str,
        transport: Literal["sse", "streamable_http", "stdio"] = "sse",
        url: str | None = None,
        command: str | None = None,
        args: list[str] | None = None,
        headers: dict[str, str] | None = None,
        env: dict[str, str] | None = None,
        timeout_s: float = 60.0,
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

        self._client: Client | None = None
        self._is_connected = False
        self._lock: asyncio.Lock | None = None

    @property
    def _session(self) -> Any:
        return self._client

    @_session.setter
    def _session(self, value: Any) -> None:
        self._client = value

    @property
    def lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @property
    def is_connected(self) -> bool:
        return self._is_connected and self._client is not None

    async def connect(self) -> None:
        """Establish connection to the MCP server via fastmcp.Client."""
        if self._is_connected and self._client is not None:
            return

        await self.close()
        try:
            if self.transport == "sse":
                if not self.url:
                    raise ValueError(f"SSE transport requires 'url' for server '{self.alias}'")
                transport_obj = SSETransport(
                    url=self.url,
                    headers=self.headers,
                    httpx_client_factory=_create_mcp_retry_http_client,
                )
            elif self.transport == "streamable_http":
                if not self.url:
                    raise ValueError(f"Streamable HTTP transport requires 'url' for server '{self.alias}'")
                transport_obj = StreamableHttpTransport(
                    url=self.url,
                    headers=self.headers,
                    httpx_client_factory=_create_mcp_retry_http_client,
                )
            elif self.transport == "stdio":
                if not self.command:
                    raise ValueError(f"Stdio transport requires 'command' for server '{self.alias}'")
                transport_obj = StdioTransport(
                    command=self.command,
                    args=self.args,
                    env=self.env,
                )
            else:
                raise ValueError(f"Unsupported MCP transport '{self.transport}'")

            client = Client(transport_obj, name=self.alias, timeout=self.timeout_s)
            await client.__aenter__()
            self._client = client
            self._is_connected = True
            logger.info("mcp.client_connected", alias=self.alias, transport=self.transport)
        except BaseException as exc:
            logger.warning("mcp.client_connect_failed", alias=self.alias, error=str(exc))
            await self.close()
            raise exc

    async def list_tools(self) -> list[Tool]:
        """List available tools from the MCP server with auto-reconnect."""
        async with self.lock:
            last_exc: BaseException | None = None
            for attempt in range(1, self.max_retries + 1):
                try:
                    if not self.is_connected:
                        await self.connect()
                    assert self._client is not None
                    res = await self._client.list_tools()
                    if hasattr(res, "tools"):
                        return res.tools  # type: ignore[no-any-return]
                    return res  # type: ignore[return-value]
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

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> CallToolResult:
        """Execute a tool call on the MCP server with auto-reconnect using per-call timeout."""
        call_timeout = timeout_s if timeout_s is not None else self.timeout_s
        async with self.lock:
            last_exc: BaseException | None = None
            for attempt in range(1, self.max_retries + 1):
                try:
                    if not self.is_connected:
                        await self.connect()
                    assert self._client is not None
                    try:
                        res = await self._client.call_tool(
                            name=name,
                            arguments=arguments or {},
                            raise_on_error=False,
                            timeout=call_timeout,
                        )
                    except TypeError as exc:
                        if "unexpected keyword argument" in str(exc):
                            res = await self._client.call_tool(
                                name=name,
                                arguments=arguments or {},
                            )
                        else:
                            raise
                    return res  # type: ignore[return-value]
                except BaseException as exc:
                    last_exc = exc
                    is_timeout = (
                        isinstance(exc, (asyncio.TimeoutError, TimeoutError))
                        or "timed out" in str(exc).lower()
                    )
                    if is_timeout:
                        logger.warning(
                            "mcp.call_tool_timeout",
                            alias=self.alias,
                            tool_name=name,
                            attempt=attempt,
                            error=str(exc),
                        )
                        raise exc

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
        client = self._client
        self._client = None
        if client is not None:
            try:
                await client.__aexit__(None, None, None)
            except Exception as exc:  # noqa: BLE001
                logger.debug("mcp.client_close_error", alias=self.alias, error=str(exc))
        logger.info("mcp.client_closed", alias=self.alias)
