"""TraceEvent — internal schema for one agent-reasoning step.

This is the canonical definition (section 1.3 of PROMPT_PLAN). It must not be
changed without updating all consumers (tracer, formatters, API routes).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


# All valid event types in the order they typically appear during a run.
TraceEventType = Literal[
    "run_start",
    "context_built",
    "llm_request",
    "llm_response",
    "thinking",
    "tool_selected",
    "tool_call",
    "tool_result",
    "memory_write",
    "reflection",
    "final_answer",
    "error",
    "run_end",
]


class TraceEvent(BaseModel):
    """One line in the reasoning trace — exactly as defined in section 1.3."""

    run_id: UUID
    seq: int = Field(..., ge=0, description="Monotonically increasing, unique within a run.")
    iteration: int = Field(..., ge=0)
    type: TraceEventType
    summary: str = Field(..., description="Single human-readable line.")
    payload: dict[str, Any] = Field(default_factory=dict)
    duration_ms: int | None = None
    created_at: datetime

    model_config = {"frozen": True}
