"""ToolRegistry with @register_tool decorator and auto-discovery."""
from __future__ import annotations

import importlib
import inspect
import pkgutil
import sys
from collections.abc import Callable
from typing import Any, Literal

from pydantic import Field, create_model

from app.config import get_settings
from app.tools.base import BaseTool, ToolSpec


class FunctionTool(BaseTool):
    """Wrapper that turns an async or sync Python function into a BaseTool."""

    def __init__(
        self,
        func: Callable[..., Any],
        name: str | None = None,
        description: str | None = None,
        source: Literal["local", "mcp", "verosight"] = "local",
        available: bool = True,
        cost_credits: int = 0,
        timeout_s: float = 30.0,
        tags: list[str] | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> None:
        self._func = func
        tool_name = name or func.__name__
        tool_desc = (description or func.__doc__ or tool_name).strip()

        if parameters is None:
            parameters = self._build_parameters(func, tool_name)

        self._spec = ToolSpec(
            name=tool_name,
            description=tool_desc,
            parameters=parameters,
            source=source,
            cost_credits=cost_credits,
            timeout_s=timeout_s,
            tags=tags or [],
            available=available,
        )

    @staticmethod
    def _build_parameters(func: Callable[..., Any], tool_name: str) -> dict[str, Any]:
        sig = inspect.signature(func)
        fields: dict[str, Any] = {}
        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue
            param_type = (
                param.annotation
                if param.annotation != inspect.Parameter.empty
                else Any
            )
            if param.default != inspect.Parameter.empty:
                fields[param_name] = (param_type, Field(default=param.default))
            else:
                fields[param_name] = (param_type, ...)

        model = create_model(f"{tool_name}_args", **fields)
        return model.model_json_schema()

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def run(self, **kwargs: Any) -> Any:
        if inspect.iscoroutinefunction(self._func):
            return await self._func(**kwargs)
        return self._func(**kwargs)


class ToolRegistry:
    """Registry for managing and discovering agent tools."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool, overwrite: bool = False) -> None:
        """Register a tool instance.

        Raises
        ------
        ValueError
            If a tool with the same name is already registered (startup duplicate detection).
        """
        name = tool.spec.name
        if name in self._tools and self._tools[name] is not tool and not overwrite:
            raise ValueError(
                f"Duplicate tool name registered: '{name}'. Tool names must be globally unique."
            )
        self._tools[name] = tool

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)

    def _is_tool_active(self, spec: ToolSpec) -> bool:
        settings = get_settings()
        if spec.name in settings.TOOLS_DISABLED:
            return False
        if spec.source in ("verosight", "mcp"):
            if not settings.VEROSIGHT_ENABLED:
                return False
            if settings.VEROSIGHT_MODE == "rest" and spec.source == "mcp":
                return False
            if settings.VEROSIGHT_MODE == "mcp" and spec.source == "verosight":
                return False
        return True

    def get(self, name: str) -> BaseTool | None:
        """Retrieve a tool by name if not disabled or filtered out by configuration."""
        tool = self._tools.get(name)
        if tool is None or not self._is_tool_active(tool.spec):
            return None
        return tool

    def list_specs(
        self,
        tags: list[str] | None = None,
        tools_allow: list[str] | None = None,
        tools_deny: list[str] | None = None,
    ) -> list[ToolSpec]:
        """List specs for all enabled tools, optionally filtered by tags, tools_allow, and tools_deny."""
        settings = get_settings()
        specs: list[ToolSpec] = []
        for _name, tool in self._tools.items():
            spec = tool.spec
            if not self._is_tool_active(spec):
                continue
            if tools_allow is not None and spec.name not in tools_allow:
                continue
            if tools_deny is not None and spec.name in tools_deny:
                continue
            if tags:
                spec_tags = set(spec.tags)
                if not set(tags).issubset(spec_tags):
                    continue

            # Prefix description if mode is "both" to avoid confusing LLM
            if settings.VEROSIGHT_ENABLED and settings.VEROSIGHT_MODE == "both":
                if spec.source == "verosight" and not spec.description.startswith("[REST]"):
                    spec = spec.model_copy(update={"description": f"[REST] {spec.description}"})
                elif spec.source == "mcp" and not spec.description.startswith("[MCP]"):
                    spec = spec.model_copy(update={"description": f"[MCP] {spec.description}"})

            specs.append(spec)
        return specs

    def to_responses_tools(
        self,
        tags: list[str] | None = None,
        tools_allow: list[str] | None = None,
        tools_deny: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return tools formatted for the OpenAI Responses API.

        Format: flat structure without nested 'function' wrapper:
        {"type": "function", "name": ..., "description": ..., "parameters": ...}
        """
        return [
            {
                "type": "function",
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            }
            for spec in self.list_specs(tags=tags, tools_allow=tools_allow, tools_deny=tools_deny)
        ]

    def to_chat_tools(
        self,
        tags: list[str] | None = None,
        tools_allow: list[str] | None = None,
        tools_deny: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return tools formatted for the OpenAI Chat Completions API.

        Format: nested structure with 'function' wrapper:
        {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                },
            }
            for spec in self.list_specs(tags=tags, tools_allow=tools_allow, tools_deny=tools_deny)
        ]

    def to_tools(
        self,
        api_style: str = "responses",
        tags: list[str] | None = None,
        tools_allow: list[str] | None = None,
        tools_deny: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return tools formatted for given api_style ('responses' | 'chat_completions')."""
        if api_style == "chat_completions":
            return self.to_chat_tools(tags=tags, tools_allow=tools_allow, tools_deny=tools_deny)
        return self.to_responses_tools(tags=tags, tools_allow=tools_allow, tools_deny=tools_deny)

    def discover(self, package_path: str = "app.tools") -> None:
        """Auto-discover and import all modules under package_path."""
        try:
            pkg = importlib.import_module(package_path)
        except ImportError:
            return

        if hasattr(pkg, "__path__"):
            for _, modname, _ in pkgutil.walk_packages(
                pkg.__path__, prefix=f"{package_path}."
            ):
                if modname in sys.modules:
                    importlib.reload(sys.modules[modname])
                else:
                    importlib.import_module(modname)

        global_reg = get_registry()
        for name, tool in global_reg._tools.items():
            self._tools[name] = tool



    def clear(self) -> None:
        """Clear all registered tools (used primarily in tests)."""
        self._tools.clear()

    def __len__(self) -> int:
        return len(self._tools)


_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    """Return the global ToolRegistry singleton instance."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry


def init_registry() -> ToolRegistry:
    """Ensure the global ToolRegistry is initialized and auto-discovered."""
    registry = get_registry()
    if len(registry) == 0:
        registry.discover()
    return registry


def reset_registry() -> ToolRegistry:
    """Reset and return the global ToolRegistry instance."""
    global _registry
    _registry = ToolRegistry()
    return _registry


def register_tool(
    name: str | None = None,
    description: str | None = None,
    source: Literal["local", "mcp", "verosight"] = "local",
    cost_credits: int = 0,
    timeout_s: float = 30.0,
    tags: list[str] | None = None,
    parameters: dict[str, Any] | None = None,
    registry: ToolRegistry | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to convert and register a Python function as a tool.

    Usage
    -----
    @register_tool(name="calculator", description="Evaluates math")
    async def calculator(expression: str) -> float:
        ...
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        tool = FunctionTool(
            func=func,
            name=name,
            description=description,
            source=source,
            cost_credits=cost_credits,
            timeout_s=timeout_s,
            tags=tags,
            parameters=parameters,
        )
        target_registry = registry if registry is not None else get_registry()
        target_registry.register(tool, overwrite=True)
        return func

    return decorator
