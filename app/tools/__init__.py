"""Tools package — modular tool system.

Adding a new tool = creating one file in app/tools/ without modifying core agent logic.
"""
from __future__ import annotations

from app.tools.base import BaseTool, ToolResult, ToolSpec, build_schema_from_pydantic
from app.tools.executor import ToolExecutor
from app.tools.registry import (
    ToolRegistry,
    get_registry,
    init_registry,
    register_tool,
    reset_registry,
)

__all__ = [
    "BaseTool",
    "ToolSpec",
    "ToolResult",
    "ToolRegistry",
    "ToolExecutor",
    "build_schema_from_pydantic",
    "get_registry",
    "init_registry",
    "register_tool",
    "reset_registry",
]
