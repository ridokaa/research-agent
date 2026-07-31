"""Async tests for all FastAPI endpoints (PROMPT 9 Definition of Done).

Uses httpx.AsyncClient with ASGITransport to test all routes, stream SSE output,
request_id middleware, global error formatting, and verify that POST /v1/agent/query
response strictly contains no reasoning/thinking/tool fields.
"""
from __future__ import annotations

import os
import uuid
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agent.loop import AgentLoop
from app.api.deps import get_session
from app.db.models import Base, Conversation, Run, RunStep, ToolInvocation
from app.main import app
from app.schemas.agent import RunResult

from app.db.session import close_engine, get_engine, init_engine
from sqlalchemy.pool import NullPool

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/rag_db",
)


@pytest_asyncio.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Provide httpx AsyncClient with app.db.session engine initialized."""
    engine = create_async_engine(DATABASE_URL, poolclass=NullPool, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def _get_test_session() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_session] = _get_test_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    await engine.dispose()


# ---------------------------------------------------------------------------
# 1. Health Endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health(client: AsyncClient) -> None:
    """GET /health must return 200 with {"status": "ok"}."""
    res = await client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_health_deps(client: AsyncClient) -> None:
    """GET /health/deps must check DB, LLM, and MCP status."""
    res = await client.get("/health/deps")
    assert res.status_code == 200
    data = res.json()
    assert "status" in data
    assert "db" in data
    assert "llm" in data
    assert "mcp" in data


# ---------------------------------------------------------------------------
# 2. Tools Endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tools(client: AsyncClient) -> None:
    """GET /v1/tools returns list of available tools."""
    res = await client.get("/v1/tools")
    assert res.status_code == 200
    tools = res.json()
    assert isinstance(tools, list)


# ---------------------------------------------------------------------------
# 3. Agent Query & Clean Response Verification (DoD Requirement)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_agent_query_clean_response(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /v1/agent/query MUST return clean response without thinking/reasoning/tools."""
    fake_run_id = uuid.uuid4()
    fake_conv_id = uuid.uuid4()

    async def mock_run(self, user_query: str, conversation_id: uuid.UUID | None = None, options: Any = None) -> RunResult:
        return RunResult(
            run_id=fake_run_id,
            conversation_id=conversation_id or fake_conv_id,
            answer="Answer: 42",
            status="success",
            iterations=1,
            latency_ms=120,
        )

    monkeypatch.setattr(AgentLoop, "run", mock_run)

    payload = {
        "query": "What is the meaning of life?",
        "options": {"max_iterations": 3, "credits_budget": 50},
    }

    res = await client.post("/v1/agent/query", json=payload)
    assert res.status_code == 200

    data = res.json()

    # Exact required top-level fields
    expected_fields = {"answer", "run_id", "conversation_id", "status", "iterations", "latency_ms"}
    assert set(data.keys()) == expected_fields
    assert data["answer"] == "Answer: 42"
    assert data["status"] == "success"
    assert data["iterations"] == 1

    # DoD STRICT VERIFICATION: Ensure NO reasoning or tool fields are returned!
    forbidden_fields = {"thinking", "reasoning", "trace", "steps", "tools", "tool_invocations", "payload"}
    for forbidden in forbidden_fields:
        assert forbidden not in data, f"Response payload leaked internal field: {forbidden}"


@pytest.mark.asyncio
async def test_post_agent_query_stream_sse(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /v1/agent/query/stream streams tokens via SSE and finishes with [DONE]."""
    fake_run_id = uuid.uuid4()
    fake_conv_id = uuid.uuid4()

    async def mock_run(self, user_query: str, conversation_id: uuid.UUID | None = None, options: Any = None) -> RunResult:
        return RunResult(
            run_id=fake_run_id,
            conversation_id=conversation_id or fake_conv_id,
            answer="Final test answer.",
            status="success",
            iterations=1,
            latency_ms=100,
        )

    monkeypatch.setattr(AgentLoop, "run", mock_run)

    payload = {"query": "Tell me a story"}
    res = await client.post("/v1/agent/query/stream", json=payload)
    assert res.status_code == 200
    assert "text/event-stream" in res.headers["content-type"]

    body_text = res.text
    assert "data: Final " in body_text or "data: test " in body_text or "data: answer. " in body_text
    assert "data: [DONE]" in body_text
    assert "thinking" not in body_text
    assert "tool_call" not in body_text


# ---------------------------------------------------------------------------
# 4. Runs, Trace, and Tools Endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runs_endpoints(
    client: AsyncClient,
) -> None:
    """Test GET /v1/runs, GET /v1/runs/{id}, /v1/runs/{id}/trace, /v1/runs/{id}/tools."""
    conv_id = uuid.uuid4()
    run_id = uuid.uuid4()

    # Seed test run and steps in DB
    conv = Conversation(id=conv_id, title="Test Conv")
    run = Run(
        id=run_id,
        conversation_id=conv_id,
        user_query="Test query",
        status="success",
        final_answer="Test final answer",
        iterations=2,
        credits_used=10,
        total_tokens=500,
        latency_ms=1500,
    )
    step1 = RunStep(
        id=uuid.uuid4(),
        run_id=run_id,
        seq=0,
        iteration=1,
        type="run_start",
        summary="Agent started",
    )
    step2 = RunStep(
        id=uuid.uuid4(),
        run_id=run_id,
        seq=1,
        iteration=1,
        type="final_answer",
        summary="Final answer emitted",
        payload={"answer": "Test final answer"},
    )
    tool_inv = ToolInvocation(
        id=uuid.uuid4(),
        run_id=run_id,
        step_seq=1,
        tool_name="verosight_search",
        source="local",
        status="success",
        credits_used=5,
    )

    seed_engine = create_async_engine(DATABASE_URL, poolclass=NullPool, echo=False)
    factory = async_sessionmaker(seed_engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        session.add_all([conv, run, step1, step2, tool_inv])
        await session.commit()
    await seed_engine.dispose()

    # 1. List runs
    res = await client.get("/v1/runs")
    print("--- STEP 1 DONE ---")
    assert res.status_code == 200
    runs_list = res.json()
    assert any(r["id"] == str(run_id) for r in runs_list)

    # 2. Get run by ID
    res_get = await client.get(f"/v1/runs/{run_id}")
    print("--- STEP 2 DONE ---")
    assert res_get.status_code == 200
    assert res_get.json()["id"] == str(run_id)

    # 3. Get run trace (JSON)
    res_trace = await client.get(f"/v1/runs/{run_id}/trace")
    print("--- STEP 3 DONE ---")
    assert res_trace.status_code == 200
    trace_events = res_trace.json()
    assert len(trace_events) == 2
    assert trace_events[0]["type"] == "run_start"
    assert trace_events[1]["type"] == "final_answer"

    # 4. Get run trace (text format)
    res_trace_text = await client.get(f"/v1/runs/{run_id}/trace?format=text")
    print("--- STEP 4 DONE ---")
    assert res_trace_text.status_code == 200
    assert "text/plain" in res_trace_text.headers["content-type"]
    assert "run_start" in res_trace_text.text

    # 5. Get run tools
    res_tools = await client.get(f"/v1/runs/{run_id}/tools")
    print("--- STEP 5 DONE ---")
    assert res_tools.status_code == 200
    tools_invs = res_tools.json()
    assert len(tools_invs) == 1
    assert tools_invs[0]["tool_name"] == "verosight_search"


@pytest.mark.asyncio
async def test_run_not_found(client: AsyncClient) -> None:
    """Non-existent run_id returns 404 with error format."""
    random_id = uuid.uuid4()
    res = await client.get(f"/v1/runs/{random_id}")
    assert res.status_code == 404
    data = res.json()
    assert data["error"]["code"] == "404"
    assert "meta" in data
    assert "request_id" in data["meta"]


# ---------------------------------------------------------------------------
# 5. Conversations Endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conversations_endpoints(
    client: AsyncClient,
) -> None:
    """Test GET /v1/conversations and GET /v1/conversations/{id}."""
    conv_id = uuid.uuid4()
    conv = Conversation(id=conv_id, title="Test Conversation Title")
    seed_engine = create_async_engine(DATABASE_URL, poolclass=NullPool, echo=False)
    factory = async_sessionmaker(seed_engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        session.add(conv)
        await session.commit()
    await seed_engine.dispose()

    res_list = await client.get("/v1/conversations")
    assert res_list.status_code == 200
    convs = res_list.json()
    assert any(c["id"] == str(conv_id) for c in convs)

    res_get = await client.get(f"/v1/conversations/{conv_id}")
    assert res_get.status_code == 200
    assert res_get.json()["title"] == "Test Conversation Title"


# ---------------------------------------------------------------------------
# 6. Request ID, Middleware & Global Exception Handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_id_middleware(client: AsyncClient) -> None:
    """X-Request-ID header is propagated or auto-generated."""
    custom_id = "req-custom-xyz-123"
    res = await client.get("/health", headers={"X-Request-ID": custom_id})
    assert res.status_code == 200
    assert res.headers["x-request-id"] == custom_id

    res_auto = await client.get("/health")
    assert res_auto.status_code == 200
    assert "x-request-id" in res_auto.headers


@pytest.mark.asyncio
async def test_validation_error_format(client: AsyncClient) -> None:
    """Invalid request body yields 422 with standard error envelope."""
    res = await client.post("/v1/agent/query", json={"invalid_field": 123})
    assert res.status_code == 422
    data = res.json()
    assert data["error"]["code"] == "VALIDATION_ERROR"
    assert "meta" in data
    assert "request_id" in data["meta"]
