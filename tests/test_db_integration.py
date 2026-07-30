"""Integration tests for the database layer (PROMPT 1 DoD).

Tests run against a real PostgreSQL database (rag_db).
Requires Docker to be running: `make up`

Test scenario (Definition of Done):
  conversation → run → 3 run_steps → get_full_trace returns correct seq order.
"""
from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base
from app.db.repositories.conversation import ConversationRepo
from app.db.repositories.memory import MemoryRepo
from app.db.repositories.run import RunRepo
from app.db.repositories.step import StepRepo

# ---------------------------------------------------------------------------
# Use the real DB URL (or override via env var in CI)
# ---------------------------------------------------------------------------
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/rag_db",
)

# ---------------------------------------------------------------------------
# Session fixture — creates tables if not present, rolls back after each test
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="function")
async def session() -> AsyncSession:  # type: ignore[override]
    """Yield an AsyncSession that rolls back all changes after each test."""
    engine = create_async_engine(DATABASE_URL, echo=False)

    # Ensure all tables exist (idempotent — won't re-create if migration was run)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as session, session.begin():
        yield session
        # Roll back so tests are isolated — nothing is permanently committed
        await session.rollback()

    await engine.dispose()


# ---------------------------------------------------------------------------
# Helper: unique IDs per test
# ---------------------------------------------------------------------------

def new_id() -> uuid.UUID:
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# Core DoD test: conversation → run → 3 steps → get_full_trace
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_trace_seq_order(session: AsyncSession) -> None:
    """
    Definition of Done:
    Create conversation → run → 3 run_steps (inserted out of order) →
    get_full_trace returns steps ordered by seq ascending.
    """
    conv_repo = ConversationRepo(session)
    run_repo = RunRepo(session)
    step_repo = StepRepo(session)

    # 1. Create conversation
    conv = await conv_repo.create(title="Test conversation")
    assert conv.id is not None

    # 2. Create run
    run = await run_repo.create(
        conversation_id=conv.id,
        user_query="What is the sentiment on topic X?",
    )
    assert run.id is not None
    assert run.status == "running"

    # 3. Append 3 run_steps (intentionally insert seq 3, 1, 2 out of order)
    await step_repo.append_step(
        run_id=run.id, seq=3, iteration=1,
        type="tool_result", summary="Tool returned 42 results",
        payload={"count": 42},
    )
    await step_repo.append_step(
        run_id=run.id, seq=1, iteration=1,
        type="llm_request", summary="Calling LLM with 2 tools",
        payload={"model": "gpt-4o-mini", "tools": 2},
    )
    await step_repo.append_step(
        run_id=run.id, seq=2, iteration=1,
        type="tool_call", summary="Calling verosight_sentiment",
        payload={"tool": "verosight_sentiment", "args": {"query": "X", "days": 7}},
    )

    # 4. get_full_trace must return steps ordered by seq ASC
    steps = await run_repo.get_full_trace(run.id)

    assert len(steps) == 3, f"Expected 3 steps, got {len(steps)}"
    seqs = [s.seq for s in steps]
    assert seqs == [1, 2, 3], f"Expected seq [1, 2, 3], got {seqs}"
    assert steps[0].type == "llm_request"
    assert steps[1].type == "tool_call"
    assert steps[2].type == "tool_result"


@pytest.mark.asyncio
async def test_bulk_append_steps(session: AsyncSession) -> None:
    """bulk_append_steps inserts multiple steps in one flush."""
    conv_repo = ConversationRepo(session)
    run_repo = RunRepo(session)
    step_repo = StepRepo(session)

    conv = await conv_repo.create(title="Bulk test conversation")
    run = await run_repo.create(
        conversation_id=conv.id,
        user_query="Bulk test query",
    )

    steps_data = [
        {"run_id": run.id, "seq": i, "iteration": 1,
         "type": "llm_response", "summary": f"Step {i}", "payload": {"seq": i}}
        for i in range(1, 6)
    ]
    inserted = await step_repo.bulk_append_steps(steps_data)

    assert len(inserted) == 5
    trace = await run_repo.get_full_trace(run.id)
    assert [s.seq for s in trace] == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_update_run_status(session: AsyncSession) -> None:
    """update_status correctly persists terminal run state."""
    conv_repo = ConversationRepo(session)
    run_repo = RunRepo(session)

    conv = await conv_repo.create(title="Status test conversation")
    run = await run_repo.create(
        conversation_id=conv.id,
        user_query="Status test query",
    )
    assert run.status == "running"

    updated = await run_repo.update_status(
        run.id,
        status="success",
        final_answer="The answer is 42.",
        iterations=3,
        credits_used=15,
        total_tokens=1024,
        latency_ms=3200,
    )
    assert updated is not None
    assert updated.status == "success"
    assert updated.final_answer == "The answer is 42."
    assert updated.iterations == 3
    assert updated.credits_used == 15
    assert updated.finished_at is not None


@pytest.mark.asyncio
async def test_execution_memory_by_iteration(session: AsyncSession) -> None:
    """MemoryRepo correctly filters and orders memory by iteration."""
    conv_repo = ConversationRepo(session)
    run_repo = RunRepo(session)
    mem_repo = MemoryRepo(session)

    conv = await conv_repo.create(title="Memory test conversation")
    run = await run_repo.create(
        conversation_id=conv.id,
        user_query="Memory test query",
    )

    # Write 3 memory items across 2 iterations
    await mem_repo.create(
        run_id=run.id, iteration=1, key="sentiment_score",
        value={"score": 0.72}, origin_tool="verosight_sentiment",
    )
    await mem_repo.create(
        run_id=run.id, iteration=1, key="trending_topics",
        value={"topics": ["X", "Y"]}, origin_tool="verosight_topics",
    )
    await mem_repo.create(
        run_id=run.id, iteration=2, key="expert_accounts",
        value={"accounts": ["@alice", "@bob"]}, origin_tool="find_experts",
    )

    iter1_items = await mem_repo.list_by_iteration(run.id, iteration=1)
    assert len(iter1_items) == 2

    all_items = await mem_repo.list_by_run(run.id)
    assert len(all_items) == 3
    # Items from iteration 1 come before iteration 2
    assert all_items[0].iteration <= all_items[-1].iteration


@pytest.mark.asyncio
async def test_cascade_delete_run(session: AsyncSession) -> None:
    """Deleting a run cascades to run_steps, tool_invocations, execution_memory."""

    conv_repo = ConversationRepo(session)
    run_repo = RunRepo(session)
    step_repo = StepRepo(session)
    mem_repo = MemoryRepo(session)

    conv = await conv_repo.create(title="Cascade test")
    run = await run_repo.create(conversation_id=conv.id, user_query="Cascade test")

    await step_repo.append_step(
        run_id=run.id, seq=1, iteration=1,
        type="run_start", summary="Start",
    )
    await mem_repo.create(
        run_id=run.id, iteration=1, key="x", value={"v": 1},
    )

    # Delete the run — cascade should remove steps and memory
    await session.delete(run)
    await session.flush()

    steps = await run_repo.get_full_trace(run.id)
    assert steps == [], "Cascade delete should remove all run_steps"

    mem = await mem_repo.list_by_run(run.id)
    assert mem == [], "Cascade delete should remove all execution_memory"
