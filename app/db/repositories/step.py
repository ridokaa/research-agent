"""StepRepo — async operations for run_steps table."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RunStep


class StepRepo:
    """Repository for :class:`~app.db.models.RunStep` entities."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def append_step(
        self,
        *,
        run_id: uuid.UUID,
        seq: int,
        iteration: int,
        type: str,
        summary: str,
        payload: dict[str, Any] | None = None,
        duration_ms: int | None = None,
    ) -> RunStep:
        """Append a single step to a run. *seq* must be unique within the run."""
        step = RunStep(
            id=uuid.uuid4(),
            run_id=run_id,
            seq=seq,
            iteration=iteration,
            type=type,
            summary=summary,
            payload=payload,
            duration_ms=duration_ms,
        )
        self._session.add(step)
        await self._session.flush()
        return step

    async def bulk_append_steps(
        self,
        steps: list[dict[str, Any]],
    ) -> list[RunStep]:
        """Bulk-insert multiple steps in a single flush.

        Each dict in *steps* must contain:
          - run_id (uuid.UUID)
          - seq    (int)
          - iteration (int)
          - type   (str)
          - summary (str)
          - payload (dict | None)  — optional
          - duration_ms (int | None) — optional
        """
        orm_steps: list[RunStep] = []
        for s in steps:
            orm_step = RunStep(
                id=uuid.uuid4(),
                run_id=s["run_id"],
                seq=s["seq"],
                iteration=s["iteration"],
                type=s["type"],
                summary=s["summary"],
                payload=s.get("payload"),
                duration_ms=s.get("duration_ms"),
            )
            self._session.add(orm_step)
            orm_steps.append(orm_step)
        await self._session.flush()
        return orm_steps

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def list_by_run(
        self,
        run_id: uuid.UUID,
        *,
        iteration: int | None = None,
    ) -> list[RunStep]:
        """List steps for a run ordered by seq ASC.

        Optionally filter by *iteration*.
        """
        stmt = (
            select(RunStep)
            .where(RunStep.run_id == run_id)
            .order_by(RunStep.seq.asc())
        )
        if iteration is not None:
            stmt = stmt.where(RunStep.iteration == iteration)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
