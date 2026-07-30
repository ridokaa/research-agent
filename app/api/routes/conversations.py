"""Conversations API routes for listing and fetching conversation history metadata."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.conversation import ConversationRepo
from app.db.session import get_session
from app.schemas.agent import ConversationResponse

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationResponse])
@router.get("/", response_model=list[ConversationResponse], include_in_schema=False)
async def list_conversations(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[ConversationResponse]:
    """List conversations ordered by most recent first."""
    repo = ConversationRepo(session)
    conversations = await repo.list(limit=limit, offset=offset)
    return [
        ConversationResponse(
            id=c.id,
            title=c.title,
            metadata=c.metadata_,
            created_at=c.created_at.isoformat() if c.created_at else None,
            updated_at=c.updated_at.isoformat() if c.updated_at else None,
        )
        for c in conversations
    ]


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> ConversationResponse:
    """Fetch conversation metadata by ID."""
    repo = ConversationRepo(session)
    c = await repo.get(conversation_id)
    if c is None:
        raise HTTPException(
            status_code=404,
            detail=f"Conversation '{conversation_id}' not found.",
        )
    return ConversationResponse(
        id=c.id,
        title=c.title,
        metadata=c.metadata_,
        created_at=c.created_at.isoformat() if c.created_at else None,
        updated_at=c.updated_at.isoformat() if c.updated_at else None,
    )
