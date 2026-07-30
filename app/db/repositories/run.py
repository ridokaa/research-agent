"""RunRepo — async CRUD for the runs table + get_full_trace."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Run, RunStep


class RunRepo:
    """Repository for :class:`~app.db.models.Run` entities."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        conversation_id: uuid.UUID,
        user_query: str,
        status: str = "running",
        run_id: uuid.UUID | None = None,
    ) -> Run:
        """Insert a new run and return the persisted object."""
        run = Run(
            id=run_id or uuid.uuid4(),
            conversation_id=conversation_id,
            user_query=user_query,
            status=status,
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def update_status(
        self,
        run_id: uuid.UUID,
        *,
        status: str,
        final_answer: str | None = None,
        iterations: int | None = None,
        credits_used: int | None = None,
        total_tokens: int | None = None,
        latency_ms: int | None = None,
        last_response_id: str | None = None,
        error: str | None = None,
        finished_at: datetime | None = None,
    ) -> Run | None:
        """Update run status and optional terminal fields. Returns None if not found."""
        run = await self.get(run_id)
        if run is None:
            return None
        run.status = status
        if final_answer is not None:
            run.final_answer = final_answer
        if iterations is not None:
            run.iterations = iterations
        if credits_used is not None:
            run.credits_used = credits_used
        if total_tokens is not None:
            run.total_tokens = total_tokens
        if latency_ms is not None:
            run.latency_ms = latency_ms
        if last_response_id is not None:
            run.last_response_id = last_response_id
        if error is not None:
            run.error = error
        run.finished_at = finished_at or datetime.now(tz=UTC)
        await self._session.flush()
        return run

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def get(self, run_id: uuid.UUID) -> Run | None:
        """Fetch a run by PK. Returns None if not found."""
        return await self._session.get(Run, run_id)

    async def list(
        self,
        *,
        conversation_id: uuid.UUID | None = None,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Run]:
        """List runs ordered by most recent first, with optional filters."""
        stmt = select(Run).order_by(Run.started_at.desc()).limit(limit).offset(offset)
        if conversation_id is not None:
            stmt = stmt.where(Run.conversation_id == conversation_id)
        if status is not None:
            stmt = stmt.where(Run.status == status)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_full_trace(self, run_id: uuid.UUID) -> list[RunStep]:
        """Return all run_steps for *run_id* ordered by seq ASC.

        This is the authoritative method for retrieving the ordered trace of
        a run — the seq column guarantees correct chronological order.
        """
        stmt = (
            select(RunStep)
            .where(RunStep.run_id == run_id)
            .order_by(RunStep.seq.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_with_steps(self, run_id: uuid.UUID) -> Run | None:
        """Fetch a run with all related steps eagerly loaded."""
        stmt = (
            select(Run)
            .where(Run.id == run_id)
            .options(selectinload(Run.run_steps))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
