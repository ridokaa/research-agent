"""ConversationRepo — async CRUD for the conversations table."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation


class ConversationRepo:
    """Repository for :class:`~app.db.models.Conversation` entities."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Conversation:
        """Insert a new conversation and return the persisted object."""
        conversation = Conversation(
            id=uuid.uuid4(),
            title=title,
            metadata_=metadata,
        )
        self._session.add(conversation)
        await self._session.flush()  # get DB-generated timestamps without committing
        return conversation

    async def update(
        self,
        conversation_id: uuid.UUID,
        *,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Conversation | None:
        """Partial-update a conversation. Returns None if not found."""
        conversation = await self.get(conversation_id)
        if conversation is None:
            return None
        if title is not None:
            conversation.title = title
        if metadata is not None:
            conversation.metadata_ = metadata
        await self._session.flush()
        return conversation

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def get(self, conversation_id: uuid.UUID) -> Conversation | None:
        """Fetch a conversation by PK. Returns None if not found."""
        result = await self._session.get(Conversation, conversation_id)
        return result

    async def list(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Conversation]:
        """List conversations ordered by most recent first."""
        stmt = (
            select(Conversation)
            .order_by(Conversation.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
