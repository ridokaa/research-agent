"""Tools API routes for listing active agent tools."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.tools.registry import get_registry

router = APIRouter(prefix="/v1/tools", tags=["tools"])


class ToolInfoResponse(BaseModel):
    name: str = Field(..., description="Unique tool name")
    source: str = Field(..., description="Tool source: 'local', 'mcp', or 'verosight'")
    description: str = Field(..., description="Human/LLM-readable description")
    cost_credits: int = Field(default=0, description="API credit cost of running tool")
    available: bool = Field(default=True, description="Whether tool service is currently reachable/online")
    parameters: dict[str, Any] = Field(default_factory=dict, description="JSON Schema for parameters")
    timeout_s: float = Field(default=30.0, description="Tool timeout in seconds")
    tags: list[str] = Field(default_factory=list, description="Tool tags")


@router.get("", response_model=list[ToolInfoResponse])
@router.get("/", response_model=list[ToolInfoResponse], include_in_schema=False)
async def list_active_tools() -> list[ToolInfoResponse]:
    """Return all currently active tools in the registry matching configuration."""
    registry = get_registry()
    specs = registry.list_specs()
    return [
        ToolInfoResponse(
            name=spec.name,
            source=spec.source,
            description=spec.description,
            cost_credits=spec.cost_credits,
            available=spec.available,
            parameters=spec.parameters,
            timeout_s=spec.timeout_s,
            tags=spec.tags,
        )
        for spec in specs
    ]
