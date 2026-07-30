"""ToolRegistry with @register_tool decorator. Implemented in PROMPT 4."""
from __future__ import annotations


class ToolRegistry:
    """Placeholder registry — full implementation in PROMPT 4."""

    def __init__(self) -> None:
        self._tools: dict[str, object] = {}

    def discover(self) -> None:
        """Auto-discover tools via pkgutil.walk_packages (PROMPT 4)."""

    def __len__(self) -> int:
        return len(self._tools)


_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry
