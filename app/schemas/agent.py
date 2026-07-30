"""Agent API and loop schemas.

PROMPT 8 implementation.
"""
from __future__ import annotations

import uuid
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class RunOptions(BaseModel):
    """Execution options for an AgentLoop run."""

    max_iterations: int = 8
    credits_budget: int = 100
    timeout_s: float = 180.0
    parallel_tools: bool = True
    store: bool = True
    tools_allow: list[str] | None = Field(default=None, description="Optional whitelist of allowed tool names")
    tools_deny: list[str] | None = Field(default=None, description="Optional blacklist of denied tool names")


class RunResult(BaseModel):
    """Result returned by AgentLoop.run.

    Contains ONLY run_id, conversation_id, answer, status, iterations, and latency_ms.
    Process trace and reasoning are accessible via trace endpoints.
    """

    run_id: UUID
    conversation_id: UUID
    answer: str
    status: Literal["success", "failed", "exhausted", "timeout"]
    iterations: int
    latency_ms: int


class AgentQueryRequest(BaseModel):
    """Request payload for POST /v1/agent/query."""

    query: str = Field(..., min_length=1, max_length=4000, description="User question (max 4000 characters)")
    conversation_id: UUID | None = None
    options: RunOptions = Field(default_factory=RunOptions)


class AgentQueryResponse(BaseModel):
    """Response payload for POST /v1/agent/query.

    CRITICAL DESIGN DECISION: Does NOT contain reasoning or tool process traces.
    """

    answer: str
    run_id: UUID
    conversation_id: UUID
    status: str
    iterations: int
    latency_ms: int


class RunResponse(BaseModel):
    """Metadata response for a run."""

    id: UUID
    conversation_id: UUID
    user_query: str
    status: str
    final_answer: str | None = None
    iterations: int = 0
    credits_used: int = 0
    total_tokens: int = 0
    latency_ms: int | None = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class ConversationResponse(BaseModel):
    """Metadata response for a conversation."""

    id: UUID
    title: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: str | None = None
    updated_at: str | None = None

