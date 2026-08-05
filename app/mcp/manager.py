"""MCPManager — multi-server discovery, connect/reconnect, caching, and fallback."""
from __future__ import annotations

import json
import time
from typing import Any

import structlog
from mcp.types import Tool

from app.config import get_settings
from app.mcp.adapter import MCPTool, mcp_tool_to_spec
from app.mcp.client import MCPClient
from app.tools.base import ToolSpec
from app.tools.registry import get_registry

logger = structlog.get_logger(__name__)

CACHE_TTL_S = 900.0  # 15 minutes cache TTL

VEROSIGHT_MCP_KNOWN_TOOLS = [
    ("search_posts", "Perform semantic search across posts"),
    ("get_post", "Get post by ID"),
    ("search_comments", "Search comments on posts"),
    ("list_profiles", "List user profiles"),
    ("get_profile", "Get profile by username or ID"),
    ("get_profile_stats", "Get statistics for a profile"),
    ("get_trending", "Get trending topics and hashtags"),
    ("get_sentiment", "Get overall sentiment analytics"),
    ("get_volume", "Get post volume analytics over time"),
    ("get_topics", "Get top topics analytics"),
    ("get_engagement_ranking", "Get profile engagement rankings"),
    ("get_compare", "Compare sentiment or stats between entities"),
    ("find_experts", "Find expert profiles on a topic"),
    ("get_sentiment_trend", "Get sentiment trend over time"),
    ("get_hashtag_trending", "Get trending hashtags"),
    ("get_mention_ranking", "Get most mentioned entities ranking"),
    ("get_best_time", "Get best time to post analytics"),
    ("get_growth", "Get profile growth metrics"),
    ("semantic_search", "Semantic search across platform data"),
    ("get_balance", "Get account API credit balance"),
]


class MCPManager:
    """Manages connections to multiple MCP servers with 15-min tool caching and graceful degradation."""

    def __init__(self) -> None:
        self._clients: dict[str, MCPClient] = {}
        self._tool_cache: dict[str, tuple[list[ToolSpec], float]] = {}

    def get_server_configs(self) -> list[dict[str, Any]]:
        """Parse server configurations from Settings."""
        settings = get_settings()

        if settings.MCP_SERVERS_JSON.strip():
            try:
                configs = json.loads(settings.MCP_SERVERS_JSON)
                if isinstance(configs, list):
                    return configs
            except Exception as exc:  # noqa: BLE001
                logger.warning("mcp.invalid_servers_json", error=str(exc))

        # Default Verosight MCP server configuration if enabled
        if settings.VEROSIGHT_ENABLED:
            return [
                {
                    "alias": "verosight",
                    "transport": settings.VEROSIGHT_MCP_TRANSPORT,
                    "url": settings.VEROSIGHT_MCP_SSE_URL,
                    "headers": {"X-API-Key": settings.VEROSIGHT_API_KEY},
                    "enabled": True,
                }
            ]

        return []

    def get_client(self, alias: str) -> MCPClient | None:
        """Retrieve an MCP client by alias."""
        return self._clients.get(alias)

    async def connect_all(self) -> None:
        """Connect to all configured enabled MCP servers and register tools into ToolRegistry."""
        settings = get_settings()
        configs = self.get_server_configs()
        registry = get_registry()
        now = time.monotonic()

        for config in configs:
            alias = config.get("alias", "default")
            enabled = config.get("enabled", True)
            if not enabled:
                continue

            transport = config.get("transport", "sse")
            url = config.get("url")
            command = config.get("command")
            args = config.get("args")
            headers = config.get("headers")
            env = config.get("env")

            # Check existing client or build new one
            client = self._clients.get(alias)
            if client is None:
                timeout_s = float(config.get("timeout_s", settings.MCP_TIMEOUT_S))
                client = MCPClient(
                    alias=alias,
                    transport=transport,
                    url=url,
                    command=command,
                    args=args,
                    headers=headers,
                    env=env,
                    timeout_s=timeout_s,
                )
                self._clients[alias] = client

            # Attempt to connect and fetch tools
            try:
                tools: list[Tool] = []
                cached_data = self._tool_cache.get(alias)
                if cached_data and (now - cached_data[1]) < CACHE_TTL_S:
                    cached_specs, _ = cached_data
                    for spec in cached_specs:
                        mcp_tool = MCPTool(
                            client=client,
                            original_tool_name=spec.name.removeprefix(f"{alias}__"),
                            spec=spec,
                        )
                        registry.register(mcp_tool, overwrite=True)
                    logger.info("mcp.tools_loaded_from_cache", alias=alias, tools=len(cached_specs))
                    continue

                tools = await client.list_tools()
                specs: list[ToolSpec] = []
                for tool in tools:
                    spec = mcp_tool_to_spec(tool, server_alias=alias, available=True)
                    specs.append(spec)
                    mcp_tool = MCPTool(
                        client=client,
                        original_tool_name=tool.name,
                        spec=spec,
                    )
                    registry.register(mcp_tool, overwrite=True)

                self._tool_cache[alias] = (specs, now)
                logger.info("mcp.server_connected", alias=alias, tools=len(specs))

            except BaseException as exc:  # noqa: BLE001
                logger.warning(
                    "mcp.server_connect_failed",
                    alias=alias,
                    error=str(exc),
                    msg="Server unavailable. Registering tools as unavailable.",
                )

                # Server offline fallback: register tools as unavailable so startup doesn't fail
                self._register_unavailable_tools(client, alias, registry)

    def _register_unavailable_tools(
        self,
        client: MCPClient,
        alias: str,
        registry: Any,
    ) -> None:
        """Register tools as unavailable for an offline server."""
        cached_data = self._tool_cache.get(alias)
        if cached_data:
            cached_specs, _ = cached_data
            for spec in cached_specs:
                unavail_spec = spec.model_copy(update={"available": False})
                mcp_tool = MCPTool(
                    client=client,
                    original_tool_name=spec.name.removeprefix(f"{alias}__"),
                    spec=unavail_spec,
                )
                registry.register(mcp_tool, overwrite=True)
            return

        if alias == "verosight":
            for tool_name, desc in VEROSIGHT_MCP_KNOWN_TOOLS:
                spec = ToolSpec(
                    name=f"{alias}__{tool_name}",
                    description=desc,
                    parameters={"type": "object", "properties": {}},
                    source="mcp",
                    cost_credits=0,
                    timeout_s=30.0,
                    tags=["mcp", alias],
                    available=False,
                )
                mcp_tool = MCPTool(
                    client=client,
                    original_tool_name=tool_name,
                    spec=spec,
                )
                registry.register(mcp_tool, overwrite=True)

    async def close_all(self) -> None:
        """Close all active MCP client connections cleanly."""
        for alias, client in self._clients.items():
            try:
                await client.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("mcp.close_failed", alias=alias, error=str(exc))
        self._clients.clear()


_manager: MCPManager | None = None


def get_mcp_manager() -> MCPManager:
    """Return the global MCPManager singleton instance."""
    global _manager
    if _manager is None:
        _manager = MCPManager()
    return _manager


def reset_mcp_manager() -> MCPManager:
    """Reset and return the global MCPManager instance."""
    global _manager
    _manager = MCPManager()
    return _manager
