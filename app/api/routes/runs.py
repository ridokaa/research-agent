"""Runs API routes for managing and inspecting run execution metadata and traces."""
from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.run import RunRepo
from app.db.repositories.tool_invocation import ToolInvocationRepo
from app.db.session import get_session
from app.logging.formatters import render_trace
from app.schemas.agent import RunResponse
from app.schemas.trace import TraceEvent

router = APIRouter(prefix="/v1/runs", tags=["runs"])


class ToolInvocationResponse(BaseModel):
    """Response model for a tool invocation."""

    id: uuid.UUID
    run_id: uuid.UUID
    step_seq: int | None = None
    tool_name: str
    source: str
    arguments: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    status: str
    error: str | None = None
    duration_ms: int | None = None
    credits_used: int = 0
    created_at: str | None = None


@router.get("", response_model=list[RunResponse])
@router.get("/", response_model=list[RunResponse], include_in_schema=False)
async def list_runs(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None, description="Optional status filter"),
    session: AsyncSession = Depends(get_session),
) -> list[RunResponse]:
    """List runs ordered by most recent first."""
    repo = RunRepo(session)
    runs = await repo.list(status=status, limit=limit, offset=offset)
    return [
        RunResponse(
            id=r.id,
            conversation_id=r.conversation_id,
            user_query=r.user_query,
            status=r.status,
            final_answer=r.final_answer,
            iterations=r.iterations,
            credits_used=r.credits_used,
            total_tokens=r.total_tokens,
            latency_ms=r.latency_ms,
            error=r.error,
            started_at=r.started_at.isoformat() if r.started_at else None,
            finished_at=r.finished_at.isoformat() if r.finished_at else None,
        )
        for r in runs
    ]


@router.get("/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> RunResponse:
    """Fetch run metadata and final answer by run ID."""
    repo = RunRepo(session)
    r = await repo.get(run_id)
    if r is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    return RunResponse(
        id=r.id,
        conversation_id=r.conversation_id,
        user_query=r.user_query,
        status=r.status,
        final_answer=r.final_answer,
        iterations=r.iterations,
        credits_used=r.credits_used,
        total_tokens=r.total_tokens,
        latency_ms=r.latency_ms,
        error=r.error,
        started_at=r.started_at.isoformat() if r.started_at else None,
        finished_at=r.finished_at.isoformat() if r.finished_at else None,
    )


@router.get("/{run_id}/trace")
async def get_run_trace(
    run_id: uuid.UUID,
    format: str | None = Query(default=None, description="Set format=text for plain text tree rendering"),
    session: AsyncSession = Depends(get_session),
) -> Any:
    """Return complete chronological trace events for a run.
    
    Returns JSON array of TraceEvent objects by default.
    If format=text, returns Unicode tree rendering as text/plain.
    """
    repo = RunRepo(session)
    run = await repo.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    steps = await repo.get_full_trace(run_id)
    events = [
        TraceEvent(
            run_id=step.run_id,
            seq=step.seq,
            iteration=step.iteration,
            type=step.type,  # type: ignore[arg-type]
            summary=step.summary,
            payload=step.payload or {},
            duration_ms=step.duration_ms,
            created_at=step.created_at,
        )
        for step in steps
    ]

    if format == "text":
        rendered = render_trace(
            events,
            run_label=str(run_id)[:8],
            query=run.user_query,
        )
        return Response(content=rendered, media_type="text/plain")

    return events


@router.get("/{run_id}/tools", response_model=list[ToolInvocationResponse])
async def get_run_tools(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> list[ToolInvocationResponse]:
    """List all tool invocations recorded for a run."""
    run_repo = RunRepo(session)
    run = await run_repo.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    tool_repo = ToolInvocationRepo(session)
    invocations = await tool_repo.list_by_run(run_id)
    return [
        ToolInvocationResponse(
            id=inv.id,
            run_id=inv.run_id,
            step_seq=inv.step_seq,
            tool_name=inv.tool_name,
            source=inv.source,
            arguments=inv.arguments,
            result=inv.result,
            status=inv.status,
            error=inv.error,
            duration_ms=inv.duration_ms,
            credits_used=inv.credits_used,
            created_at=inv.created_at.isoformat() if inv.created_at else None,
        )
        for inv in invocations
    ]
