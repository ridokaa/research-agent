"""MCPManager — multi-server connect/reconnect. Implemented in PROMPT 6."""
from __future__ import annotations


class MCPManager:
    """Placeholder MCP manager — full implementation in PROMPT 6."""

    async def connect_all(self) -> None:
        """Connect to all configured MCP servers."""

    async def close_all(self) -> None:
        """Close all MCP server connections."""


_manager: MCPManager | None = None


def get_mcp_manager() -> MCPManager:
    global _manager
    if _manager is None:
        _manager = MCPManager()
    return _manager
