"""MemoryRepo — async operations for execution_memory table."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ExecutionMemory


class MemoryRepo:
    """Repository for :class:`~app.db.models.ExecutionMemory` entities."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        run_id: uuid.UUID,
        iteration: int,
        key: str,
        value: dict[str, Any] | None = None,
        origin_tool: str | None = None,
    ) -> ExecutionMemory:
        """Write a single memory item for the given run & iteration."""
        mem = ExecutionMemory(
            id=uuid.uuid4(),
            run_id=run_id,
            iteration=iteration,
            key=key,
            value=value,
            origin_tool=origin_tool,
        )
        self._session.add(mem)
        await self._session.flush()
        return mem

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def get(self, memory_id: uuid.UUID) -> ExecutionMemory | None:
        """Fetch a single memory item by PK."""
        return await self._session.get(ExecutionMemory, memory_id)

    async def list_by_run(
        self,
        run_id: uuid.UUID,
        *,
        limit: int = 200,
    ) -> list[ExecutionMemory]:
        """List all memory items for a run ordered by (iteration, created_at)."""
        stmt = (
            select(ExecutionMemory)
            .where(ExecutionMemory.run_id == run_id)
            .order_by(ExecutionMemory.iteration.asc(), ExecutionMemory.created_at.asc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_iteration(
        self,
        run_id: uuid.UUID,
        iteration: int,
    ) -> list[ExecutionMemory]:
        """List memory items for a specific iteration of a run."""
        stmt = (
            select(ExecutionMemory)
            .where(
                ExecutionMemory.run_id == run_id,
                ExecutionMemory.iteration == iteration,
            )
            .order_by(ExecutionMemory.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
