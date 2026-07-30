"""ToolInvocationRepo — async operations for tool_invocations table."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ToolInvocation


class ToolInvocationRepo:
    """Repository for :class:`~app.db.models.ToolInvocation` entities."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_invocation(
        self,
        *,
        run_id: uuid.UUID,
        tool_name: str,
        source: str = "local",
        step_seq: int | None = None,
        arguments: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        status: str = "success",
        error: str | None = None,
        duration_ms: int | None = None,
        credits_used: int = 0,
    ) -> ToolInvocation:
        """Record a single tool invocation."""
        invocation = ToolInvocation(
            id=uuid.uuid4(),
            run_id=run_id,
            step_seq=step_seq,
            tool_name=tool_name,
            source=source,
            arguments=arguments,
            result=result,
            status=status,
            error=error,
            duration_ms=duration_ms,
            credits_used=credits_used,
        )
        self._session.add(invocation)
        await self._session.flush()
        return invocation

    async def list_by_run(
        self,
        run_id: uuid.UUID,
    ) -> list[ToolInvocation]:
        """List tool invocations for a run ordered by created_at ASC."""
        stmt = (
            select(ToolInvocation)
            .where(ToolInvocation.run_id == run_id)
            .order_by(ToolInvocation.created_at.asc())
        )
        res = await self._session.execute(stmt)
        return list(res.scalars().all())
