"""Security unit tests for API key masking, SQL injection prevention, rate limiting, and query length validation."""
from __future__ import annotations

import os
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import get_session
from app.api.middleware import clear_rate_limit_records
from app.db.models import Base
from app.logging.setup import _sanitize_secrets
from app.logging.tracer import _redact
from app.main import app
from app.schemas.agent import AgentQueryRequest
from app.tools.builtin.db_query import _validate_sql_query

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/rag_db",
)


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    clear_rate_limit_records()
    yield
    clear_rate_limit_records()


# ---------------------------------------------------------------------------
# 1. API Key Redaction Tests
# ---------------------------------------------------------------------------

def test_secret_redaction_in_tracer():
    """Verify secret patterns and sensitive keys are redacted by _redact."""
    payload = {
        "api_key": "sk-1234567890abcdef",
        "verosight_key": "vlt_live_9876543210",
        "authorization": "Bearer secret_token_xyz",
        "user_query": "My secret is sk-secretkey123",
        "nested": {
            "token": "vlt_nested_secret",
        },
    }
    redacted = _redact(payload)
    assert redacted["api_key"] == "***"
    assert redacted["verosight_key"] == "***"
    assert redacted["authorization"] == "***"
    assert "sk-" not in str(redacted)
    assert "vlt_" not in str(redacted)


def test_structlog_secret_sanitizer():
    """Verify structlog _sanitize_secrets processor strips API key patterns."""
    event_dict = {
        "event": "llm.request",
        "headers": {"Authorization": "Bearer sk-proj-123456789"},
        "api_key": "vlt_live_abcdef123",
    }
    sanitized = _sanitize_secrets(None, "info", event_dict)
    assert "sk-proj" not in str(sanitized)
    assert sanitized["api_key"] == "[REDACTED]"


# ---------------------------------------------------------------------------
# 2. SQL Parser & db_query Hardening Tests
# ---------------------------------------------------------------------------

def test_db_query_sql_parsing_allowed():
    """Verify valid read-only SELECT and WITH queries pass and get forced LIMIT 100."""
    q1 = "SELECT * FROM conversations"
    res1 = _validate_sql_query(q1)
    assert "LIMIT 100" in res1

    q2 = "SELECT id, title FROM runs LIMIT 10"
    res2 = _validate_sql_query(q2)
    assert res2 == "SELECT id, title FROM runs LIMIT 10"

    q3 = "SELECT id FROM runs LIMIT 500"
    res3 = _validate_sql_query(q3)
    assert "LIMIT 100" in res3

    q4 = "WITH recent_runs AS (SELECT * FROM runs) SELECT * FROM recent_runs"
    res4 = _validate_sql_query(q4)
    assert "LIMIT 100" in res4


def test_db_query_forbidden_operations():
    """Verify DDL, DML, and multi-statement queries raise ValueError."""
    forbidden_queries = [
        "INSERT INTO conversations (id) VALUES ('123')",
        "UPDATE runs SET status='failed'",
        "DELETE FROM messages",
        "DROP TABLE runs",
        "ALTER TABLE runs ADD COLUMN test text",
        "TRUNCATE TABLE messages",
        "SELECT * FROM runs; DELETE FROM runs;",
        "EXEC sp_help",
    ]
    for fq in forbidden_queries:
        with pytest.raises(ValueError):
            _validate_sql_query(fq)


def test_db_query_table_whitelist():
    """Verify non-whitelisted table access raises ValueError."""
    with pytest.raises(ValueError, match="Access to table 'users' is forbidden"):
        _validate_sql_query("SELECT * FROM users")

    with pytest.raises(ValueError, match="Access to table 'pg_shadow' is forbidden"):
        _validate_sql_query("SELECT * FROM conversations JOIN pg_shadow ON true")


# ---------------------------------------------------------------------------
# 3. Rate Limiting Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rate_limit_per_ip():
    """Verify rate_limit_middleware blocks requests exceeding rate limit per IP."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def _test_session() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _test_session
    try:
        from app.config import get_settings
        settings = get_settings()
        orig_limit = settings.RATE_LIMIT_PER_MINUTE
        settings.RATE_LIMIT_PER_MINUTE = 5

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                for i in range(5):
                    resp = await ac.post("/v1/agent/query", json={"query": "test query"})
                    assert resp.status_code != 429

                exceeded_resp = await ac.post("/v1/agent/query", json={"query": "test query"})
                assert exceeded_resp.status_code == 429
                data = exceeded_resp.json()
                assert data["error"]["code"] == 429
                assert "Rate limit exceeded" in data["error"]["message"]
        finally:
            settings.RATE_LIMIT_PER_MINUTE = orig_limit
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


# ---------------------------------------------------------------------------
# 4. User Query Length Validation Tests
# ---------------------------------------------------------------------------

def test_query_length_validation():
    """Verify Pydantic validates query length boundaries."""
    req = AgentQueryRequest(query="Hello world")
    assert req.query == "Hello world"

    with pytest.raises(ValueError):
        AgentQueryRequest(query="")

    long_query = "a" * 4001
    with pytest.raises(ValueError):
        AgentQueryRequest(query=long_query)
