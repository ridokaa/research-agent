"""BaseTool + ToolSpec + ToolResult contracts."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel


class ToolSpec(BaseModel):
    name: str  # unik global, snake_case
    description: str  # dibaca LLM — tulis sejelas mungkin
    parameters: dict[str, Any]  # JSON Schema
    source: Literal["local", "mcp", "verosight"]
    cost_credits: int = 0  # untuk budget guard
    timeout_s: float = 30.0
    tags: list[str] = []
    available: bool = True


class ToolResult(BaseModel):
    tool_name: str
    status: Literal["success", "error", "timeout", "empty"]
    data: Any | None = None
    error: str | None = None
    duration_ms: int
    credits_used: int = 0
    truncated: bool = False  # true jika hasil dipotong sebelum masuk konteks
    raw_data: Any | None = None  # respons penuh sebelum di-summarize untuk DB recording



class BaseTool(ABC):
    """Abstract base class for all agent tools."""

    @property
    @abstractmethod
    def spec(self) -> ToolSpec:
        """Return the tool's specification."""

    @abstractmethod
    async def run(self, **kwargs: Any) -> Any:
        """Execute the tool with provided arguments."""


def build_schema_from_pydantic(model: type[BaseModel]) -> dict[str, Any]:
    """Generate JSON Schema parameter dictionary from a Pydantic model."""
    return model.model_json_schema()
