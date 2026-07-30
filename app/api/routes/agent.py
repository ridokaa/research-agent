"""Agent API routes for querying the agent loop."""
from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.loop import AgentLoop
from app.db.session import get_session
from app.schemas.agent import AgentQueryRequest, AgentQueryResponse

router = APIRouter(prefix="/v1/agent", tags=["agent"])


@router.post(
    "/query",
    response_model=AgentQueryResponse,
    summary="Execute agent query synchronously",
    description=(
        "Submits a prompt to the agentic loop. Executes reasoning and tools internally. "
        "Returns ONLY the final verified answer and execution metadata. "
        "Process trace and internal reasoning steps are NEVER included in this response."
    ),
    responses={
        200: {
            "description": "Final answer response (without internal thinking/tool events).",
            "content": {
                "application/json": {
                    "example": {
                        "answer": "CEO Verosight adalah Jane Doe.",
                        "run_id": "987e6543-e89b-12d3-a456-426614174999",
                        "conversation_id": "123e4567-e89b-12d3-a456-426614174000",
                        "status": "success",
                        "iterations": 2,
                        "latency_ms": 1450,
                    }
                }
            },
        }
    },
)
async def query_agent(
    req: AgentQueryRequest,
    session: AsyncSession = Depends(get_session),
) -> AgentQueryResponse:
    """Run agent query and return clean final answer."""
    loop = AgentLoop(session=session)
    result = await loop.run(
        user_query=req.query,
        conversation_id=req.conversation_id,
        options=req.options,
    )
    return AgentQueryResponse(
        answer=result.answer,
        run_id=result.run_id,
        conversation_id=result.conversation_id,
        status=result.status,
        iterations=result.iterations,
        latency_ms=result.latency_ms,
    )


@router.post(
    "/query/stream",
    summary="Execute agent query with SSE answer streaming",
    description=(
        "Runs the agent loop and streams the final answer tokens via Server-Sent Events (SSE). "
        "Only final answer tokens and the 'done' event are emitted. "
        "No internal thinking or tool execution events are emitted."
    ),
)
async def query_agent_stream(
    req: AgentQueryRequest,
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Run agent query and stream final answer via SSE."""

    async def event_generator() -> AsyncGenerator[str, None]:
        loop = AgentLoop(session=session)
        result = await loop.run(
            user_query=req.query,
            conversation_id=req.conversation_id,
            options=req.options,
        )

        # Stream final answer words/tokens
        words = result.answer.split(" ")
        for i, word in enumerate(words):
            chunk = word if i == len(words) - 1 else f"{word} "
            yield f"data: {chunk}\n\n"
            await asyncio.sleep(0.01)

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
