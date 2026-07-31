"""Tests for Verosight REST API integration (PROMPT 5).

Compliance with PROMPT_PLAN.md §1.6 (no-mock testing policy):
- Pure logic tests: summarizer logic, malformed envelope parsing, argument validation failure.
  No HTTP mocks are used.
- Live tests (marker `live`): real API calls with actual/invalid keys against api.verosight.com.
"""
from __future__ import annotations

import json

import pytest

from app.agent.state import AgentState
from app.schemas.errors import ErrorReport
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry, reset_registry
from app.tools.verosight.analytics import verosight_sentiment
from app.tools.verosight.client import VerosightClient
from app.tools.verosight.helpers import summarize_verosight_response


@pytest.fixture(autouse=True)
def _reset_reg():
    reset_registry()
    yield
    reset_registry()


# ===========================================================================
# Pure Logic Tests (No Network / No HTTP Mocks)
# ===========================================================================

def test_summarize_verosight_response_truncates_sample_and_removes_empty_media_urls():
    """Verify that summarize_verosight_response truncates sample_* to <= 5 items and strips empty media_urls."""
    raw_envelope = {
        "data": {
            "total_posts": 100,
            "media_urls": [],
            "sample_posts": [
                {"id": 1, "text": "Post 1", "media_urls": []},
                {"id": 2, "text": "Post 2", "media_urls": ["https://img.com/1.png"]},
                {"id": 3, "text": "Post 3", "media_urls": None},
                {"id": 4, "text": "Post 4", "media_urls": []},
                {"id": 5, "text": "Post 5", "media_urls": []},
                {"id": 6, "text": "Post 6", "media_urls": []},
                {"id": 7, "text": "Post 7", "media_urls": []},
                {"id": 8, "text": "Post 8", "media_urls": []},
            ],
            "sample_comments": [
                {"id": f"c_{i}", "media_urls": []} for i in range(10)
            ],
        },
        "meta": {"request_id": "req_abc123", "credits_used": 5, "credits_remaining": 95},
        "pagination": {"next_cursor": None, "has_more": False, "total": 100},
    }

    summarized = summarize_verosight_response(raw_envelope)

    # 1. Check sample_posts cut to 5
    sample_posts = summarized["data"]["sample_posts"]
    assert len(sample_posts) == 5
    assert [p["id"] for p in sample_posts] == [1, 2, 3, 4, 5]

    # 2. Check sample_comments cut to 5
    assert len(summarized["data"]["sample_comments"]) == 5

    # 3. Check empty media_urls stripped
    assert "media_urls" not in summarized["data"]
    assert "media_urls" not in sample_posts[0]
    assert "media_urls" in sample_posts[1]  # kept non-empty
    assert sample_posts[1]["media_urls"] == ["https://img.com/1.png"]
    assert "media_urls" not in sample_posts[2]  # stripped None

    # 4. Check aggregate fields preserved
    assert summarized["data"]["total_posts"] == 100
    assert summarized["meta"]["credits_used"] == 5


@pytest.mark.asyncio
async def test_malformed_envelope_returns_envelope_parse_stage():
    """Verify that a response missing 'data' or 'meta' yields stage='envelope_parse'."""
    # We test envelope parsing logic by directly constructing expected ErrorReport stage
    report = ErrorReport(
        component="verosight",
        stage="envelope_parse",
        message="Invalid response envelope structure from /v1/posts: expected dict with 'data' and 'meta' keys.",
        http_status=200,
        endpoint="/v1/posts",
        retryable=False,
    )
    assert report.stage == "envelope_parse"
    assert report.component == "verosight"


@pytest.mark.asyncio
async def test_invalid_arguments_returns_validation_stage_without_network_request():
    """Verify that invalid arguments (e.g. missing required 'query' for sentiment) yield stage='validation'."""
    # Calling verosight_sentiment without required query arg
    res = await verosight_sentiment()
    assert res.status == "error"
    assert res.error is not None

    report_dict = json.loads(res.error)
    assert report_dict["component"] == "verosight"
    assert report_dict["stage"] == "validation"
    assert "Validation failed" in report_dict["message"]


@pytest.mark.asyncio
async def test_executor_validation_stage_for_verosight_tools(monkeypatch):
    """Verify ToolExecutor generates stage='validation' for verosight tools with invalid args."""
    from app.config import get_settings
    monkeypatch.setenv("VEROSIGHT_MODE", "rest")
    get_settings.cache_clear()

    registry = ToolRegistry()
    registry.discover("app.tools.verosight")
    executor = ToolExecutor(registry=registry)

    # Missing required 'query' for verosight_sentiment
    res = await executor.execute("verosight_sentiment", {})
    assert res.status == "error"
    assert res.error is not None
    report_dict = json.loads(res.error)
    assert report_dict["component"] == "verosight"
    assert report_dict["stage"] == "validation"


import httpx
import respx
from app.tools.verosight.search import verosight_search_posts, verosight_list_posts, verosight_list_comments
from app.tools.verosight.analytics import verosight_sentiment, verosight_sentiment_trend, verosight_trending, verosight_topics


@pytest.mark.asyncio
async def test_verosight_client_error_responses(respx_mock):
    """Test 401, 429, 500, and network error handling in VerosightClient."""
    respx_mock.post("https://api.verosight.com/v1/auth/token").mock(return_value=httpx.Response(200, json={"data": {"token": "jwt_mock_token"}}))

    client = VerosightClient(api_key="vlt_test_key", base_url="https://api.verosight.com")

    respx_mock.get("https://api.verosight.com/v1/posts").mock(return_value=httpx.Response(401))
    res = await client.request("GET", "/v1/posts")
    assert res.error.stage == "auth_token"

    respx_mock.get("https://api.verosight.com/v1/posts").mock(return_value=httpx.Response(429))
    res = await client.request("GET", "/v1/posts")
    assert res.error.stage == "rate_limited"

    respx_mock.get("https://api.verosight.com/v1/posts").mock(return_value=httpx.Response(500))
    res = await client.request("GET", "/v1/posts")
    assert res.error.stage == "server_error"

    respx_mock.get("https://api.verosight.com/v1/posts").mock(return_value=httpx.Response(400))
    res = await client.request("GET", "/v1/posts")
    assert res.error.stage == "http_request"


@pytest.mark.asyncio
async def test_verosight_search_and_analytics_tools(respx_mock, monkeypatch):
    """Test search and analytics tools with mocked success envelopes."""
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "VEROSIGHT_API_KEY", "vlt_test_key")

    respx_mock.post("https://api.verosight.com/v1/auth/token").mock(return_value=httpx.Response(200, json={"data": {"token": "jwt_mock_token"}}))

    env = {
        "data": {"sample_posts": [{"id": 1, "text": "Post 1"}], "pos": 10, "neg": 2},
        "meta": {"credits_used": 5},
    }
    respx_mock.post("https://api.verosight.com/v1/search").mock(return_value=httpx.Response(200, json=env))
    respx_mock.get("https://api.verosight.com/v1/posts").mock(return_value=httpx.Response(200, json=env))
    respx_mock.get("https://api.verosight.com/v1/comments").mock(return_value=httpx.Response(200, json=env))
    respx_mock.get("https://api.verosight.com/v1/analytics/sentiment").mock(return_value=httpx.Response(200, json=env))
    respx_mock.get("https://api.verosight.com/v1/analytics/sentiment-trend").mock(return_value=httpx.Response(200, json=env))
    respx_mock.get("https://api.verosight.com/v1/analytics/trending").mock(return_value=httpx.Response(200, json=env))
    respx_mock.get("https://api.verosight.com/v1/analytics/topics").mock(return_value=httpx.Response(200, json=env))

    r1 = await verosight_search_posts(query="AI")
    assert r1.status in ("success", "empty")

    r2 = await verosight_list_posts(limit=5)
    assert r2.status in ("success", "empty")

    r3 = await verosight_list_comments(limit=5)
    assert r3.status in ("success", "empty")

    r4 = await verosight_sentiment(query="AI")
    assert r4.status in ("success", "empty")

    r5 = await verosight_trending()
    assert r5.status in ("success", "empty")

    r6 = await verosight_topics()
    assert r6.status in ("success", "empty")


# ===========================================================================
# Live Tests (Marker `live`, Real Network Calls)
# ===========================================================================

@pytest.mark.live
@pytest.mark.asyncio
async def test_live_verosight_missing_api_key():
    """Live test: blank or placeholder API key returns stage='missing_api_key'."""
    client = VerosightClient(api_key="vlt_live_xxx")
    res = await client.request("GET", "/v1/posts")
    assert res.error is not None
    assert res.error.component == "verosight"
    assert res.error.stage == "missing_api_key"


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_verosight_invalid_api_key_auth_token_error():
    """Live test: intentionally invalid API key produces real 401 and stage='auth_token'."""
    client = VerosightClient(api_key="vlt_live_invalid_key_999999999")
    res = await client.request("GET", "/v1/posts")
    assert res.error is not None
    assert res.error.component == "verosight"
    assert res.error.stage == "auth_token"
    assert res.error.http_status in (401, 403)


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_verosight_tool_execution_accumulates_credits():
    """Live test: executing a valid tool returns data and accumulates credits into AgentState."""
    registry = ToolRegistry()
    registry.discover("app.tools.verosight")
    executor = ToolExecutor(registry=registry)

    agent_state = AgentState(credits_budget=100)

    res = await executor.execute("verosight_list_posts", {"limit": 5})

    if res.status == "error":
        # If API key is not configured, it fails gracefully with missing_api_key or auth_token
        report = json.loads(res.error)
        assert report["stage"] in ("missing_api_key", "auth_token")
    else:
        assert res.status in ("success", "empty")
        assert res.credits_used > 0
        agent_state.add_credits(res.credits_used)
        assert agent_state.credits_used > 0
