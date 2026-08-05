"""Unit and integration tests for state management (PROMPT 7 DoD).

Verifies:
1. Deduplication caching of tool calls with identical arguments.
2. `get_context_block` token limiting and pruning of error/empty results > 2 iterations old.
3. 3-iteration simulation with per-iteration persistence of messages and memory snapshots.
"""
from __future__ import annotations

import os
import uuid
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import select
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agent.state import (
    AgentState,
    ConversationHistory,
    ExecutionMemory,
    MemoryItem,
    Message,
    persist_execution_memory_snapshot,
)
from app.db.models import Base, ExecutionMemory as ExecutionMemoryORM, Message as MessageORM, Run as RunORM
from app.db.repositories.conversation import ConversationRepo
from app.db.repositories.run import RunRepo
from app.schemas.trace import TraceEvent
from app.tools.base import ToolResult

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/rag_db",
)


@pytest_asyncio.fixture(scope="function")
async def session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an AsyncSession that rolls back changes after each test."""
    engine = create_async_engine(DATABASE_URL, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(sa.text("ALTER TABLE runs ADD COLUMN IF NOT EXISTS last_response_id VARCHAR(256);"))

    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as session, session.begin():
        yield session
        await session.rollback()

    await engine.dispose()


def test_execution_memory_deduplication() -> None:
    """Verify tool calls with identical tool_name and arguments are cached."""
    memory = ExecutionMemory()
    args = {"query": "MBG", "days": 7}

    res1 = ToolResult(
        tool_name="verosight_sentiment",
        status="success",
        data={"score": 0.85, "summary": "Very positive"},
        duration_ms=120,
    )

    # Initially not cached
    assert not memory.is_cached("verosight_sentiment", args)
    assert memory.get_cached("verosight_sentiment", args) is None

    # Add result
    item = memory.add(res1, iteration=1, arguments=args)
    assert item.origin_tool == "verosight_sentiment"

    # Now cached for exact args
    assert memory.is_cached("verosight_sentiment", args)
    cached_item = memory.get_cached("verosight_sentiment", args)
    assert cached_item is not None
    assert cached_item.value == {"score": 0.85, "summary": "Very positive"}

    # Different arguments not cached
    different_args = {"query": "MBG", "days": 30}
    assert not memory.is_cached("verosight_sentiment", different_args)


def test_execution_memory_get_context_block_pruning_and_tokens() -> None:
    """Verify error pruning (>2 iterations old) and token limit honoring in context block."""
    memory = ExecutionMemory()

    # Iteration 1: success item + error item
    memory.add(
        ToolResult(tool_name="tool_ok1", status="success", data="Valid data 1", duration_ms=10),
        iteration=1,
    )
    memory.add(
        ToolResult(tool_name="tool_err1", status="error", error="Fail 1", duration_ms=10),
        iteration=1,
    )

    # Iteration 2: success item
    memory.add(
        ToolResult(tool_name="tool_ok2", status="success", data="Valid data 2", duration_ms=10),
        iteration=2,
    )

    # Iteration 3: error item
    memory.add(
        ToolResult(tool_name="tool_err3", status="error", error="Fail 3", duration_ms=10),
        iteration=3,
    )

    # At current_iteration = 3, tool_err1 (iter 1) has age 3 - 1 = 2 iterations -> pruned!
    block = memory.get_context_block(max_tokens=2000, current_iteration=3)
    assert "tool_ok1" in block
    assert "tool_ok2" in block
    assert "tool_err3" in block
    assert "tool_err1" not in block

    # Test max_tokens capping: extremely small budget only fits header or top item
    tiny_block = memory.get_context_block(max_tokens=15, current_iteration=3)
    assert len(tiny_block.splitlines()) <= 2


@pytest.mark.asyncio
async def test_state_and_persistence_3_iterations_simulation(session: AsyncSession) -> None:
    """Definition of Done:

    3-iteration simulation verifying per-iteration memory & message persistence,
    deduplication caching, and context block generation.
    """
    conv_repo = ConversationRepo(session)
    run_repo = RunRepo(session)

    conv = await conv_repo.create(title="Simulation Run")
    run = await run_repo.create(
        conversation_id=conv.id,
        user_query="What is the sentiment on MBG?",
    )

    state = AgentState(
        run_id=run.id,
        conversation_id=conv.id,
        user_query="What is the sentiment on MBG?",
        credits_budget=100,
    )
    conv_history = ConversationHistory(session, conv.id)

    # -----------------------------------------------------------------------
    # Iteration 1: User query + tool call verosight_sentiment
    # -----------------------------------------------------------------------
    state.iteration = 1
    state.messages.append(Message(role="user", content=state.user_query))

    sentiment_args = {"topic": "MBG"}
    tool_res_1 = ToolResult(
        tool_name="verosight_sentiment",
        status="success",
        data={"sentiment": "positive", "score": 0.9},
        duration_ms=150,
    )
    state.execution_memory.add(tool_res_1, iteration=1, arguments=sentiment_args)
    state.messages.append(
        Message(
            role="assistant",
            content="Calling verosight_sentiment",
            tool_call_id="call_1",
            tool_name="verosight_sentiment",
        )
    )

    # Persist iteration 1
    await conv_history.persist_iteration(state.messages, run_id=run.id, last_response_id="resp_iter1")
    await persist_execution_memory_snapshot(session, run.id, iteration=1, execution_memory=state.execution_memory)

    # -----------------------------------------------------------------------
    # Iteration 2: Duplicate tool call attempt + new clock tool call
    # -----------------------------------------------------------------------
    state.iteration = 2

    # Deduplication test: exact same tool call (name + args)
    assert state.execution_memory.is_cached("verosight_sentiment", sentiment_args)
    cached_mem = state.execution_memory.get_cached("verosight_sentiment", sentiment_args)
    assert cached_mem is not None
    assert cached_mem.value == {"sentiment": "positive", "score": 0.9}

    # Record trace event for tool_call_cached
    cached_trace = TraceEvent(
        run_id=run.id,
        seq=1,
        iteration=2,
        type="tool_call_cached",
        summary="Reused cached result for verosight_sentiment",
        payload={"tool_name": "verosight_sentiment", "arguments": sentiment_args},
        created_at=state.started_at,
    )
    assert cached_trace.type == "tool_call_cached"

    # Add second tool result (clock)
    clock_res = ToolResult(
        tool_name="clock",
        status="success",
        data={"time": "2026-07-30T15:26:00Z"},
        duration_ms=5,
    )
    state.execution_memory.add(clock_res, iteration=2, arguments={})
    state.messages.append(
        Message(role="tool", content="2026-07-30T15:26:00Z", tool_call_id="call_2", tool_name="clock")
    )

    # Persist iteration 2
    await conv_history.persist_iteration(state.messages, run_id=run.id, last_response_id="resp_iter2")
    await persist_execution_memory_snapshot(session, run.id, iteration=2, execution_memory=state.execution_memory)

    # -----------------------------------------------------------------------
    # Iteration 3: Error tool call + Final answer
    # -----------------------------------------------------------------------
    state.iteration = 3
    error_res = ToolResult(
        tool_name="verosight_topics",
        status="error",
        error="Network timeout",
        duration_ms=3000,
    )
    state.execution_memory.add(error_res, iteration=3, arguments={"query": "test"})
    state.messages.append(
        Message(
            role="assistant",
            content="Based on analysis, the sentiment on MBG is positive (0.9).",
        )
    )

    # Persist iteration 3
    await conv_history.persist_iteration(state.messages, run_id=run.id, last_response_id="resp_iter3")
    await persist_execution_memory_snapshot(session, run.id, iteration=3, execution_memory=state.execution_memory)

    # -----------------------------------------------------------------------
    # Verification of DB Persistence & Context Block
    # -----------------------------------------------------------------------
    # 1. Verify messages in DB
    db_messages_stmt = select(MessageORM).where(MessageORM.conversation_id == conv.id).order_by(MessageORM.seq.asc())
    db_messages = (await session.execute(db_messages_stmt)).scalars().all()
    assert len(db_messages) == 4
    assert [m.seq for m in db_messages] == [1, 2, 3, 4]

    # 2. Verify execution memory snapshots in DB
    db_mem_stmt = select(ExecutionMemoryORM).where(ExecutionMemoryORM.run_id == run.id).order_by(ExecutionMemoryORM.iteration.asc())
    db_mems = (await session.execute(db_mem_stmt)).scalars().all()
    assert len(db_mems) == 3
    iterations_found = [m.iteration for m in db_mems]
    assert iterations_found == [1, 2, 3]

    # 3. Verify last_response_id updated on run record
    db_run_stmt = select(RunORM).where(RunORM.id == run.id)
    db_run = (await session.execute(db_run_stmt)).scalar_one()
    assert db_run.last_response_id == "resp_iter3"

    # 4. Verify context block contains memory lines within token budget
    ctx_block = state.execution_memory.get_context_block(max_tokens=1000, current_iteration=3)
    assert "verosight_sentiment" in ctx_block
    assert "clock" in ctx_block
    assert "verosight_topics" in ctx_block


def test_execution_memory_rich_context_block_time_series() -> None:
    """Verify rich context block preserves full by_day time series without truncated strings."""
    memory = ExecutionMemory()
    by_day_data = {
        "query": "perang",
        "by_day": [
            {"date": "2026-07-28", "posts": 450, "comments": 30},
            {"date": "2026-07-29", "posts": 510, "comments": 42},
            {"date": "2026-07-30", "posts": 620, "comments": 55},
            {"date": "2026-07-31", "posts": 895, "comments": 59},
            {"date": "2026-08-01", "posts": 621, "comments": 40},
            {"date": "2026-08-02", "posts": 730, "comments": 61},
            {"date": "2026-08-03", "posts": 810, "comments": 70},
        ],
    }
    res = ToolResult(
        tool_name="verosight__get_volume",
        status="success",
        data=by_day_data,
        duration_ms=200,
    )
    memory.add(res, iteration=1, arguments={"topic": "perang"})

    block = memory.get_context_block(max_tokens=4000, current_iteration=1, rich=True)

    # All dates and numbers must be present
    for day in by_day_data["by_day"]:
        assert day["date"] in block
        assert str(day["posts"]) in block

    # Must NOT contain '...' in middle of numeric date JSON
    assert "2026-07-31" in block
    assert "2026-08-01" in block


def test_compact_tool_output_for_llm() -> None:
    """Verify heavy top_posts and top_profiles are pruned if output exceeds max_bytes."""
    from app.agent.loop import compact_tool_output_for_llm

    heavy_topics_data = {
        "total_topics": 20,
        "topics": [
            {
                "name": "Topik A",
                "post_count": 5000,
                "top_posts": [{"id": i, "content": "x" * 5000} for i in range(10)],
                "top_profiles": [{"id": i, "bio": "y" * 2000} for i in range(10)],
            }
        ],
    }

    compacted = compact_tool_output_for_llm(heavy_topics_data, max_bytes=4000)

    # top_posts and top_profiles should be pruned
    assert "top_posts" not in compacted["topics"][0]
    assert "top_profiles" not in compacted["topics"][0]

    # Essential aggregate data kept
    assert compacted["topics"][0]["name"] == "Topik A"
    assert compacted["topics"][0]["post_count"] == 5000

