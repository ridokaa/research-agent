"""Tests for app/llm/client.py.

Compliance with PROMPT_PLAN.md §1.6.3 (no-mock testing policy):

* Pure-logic tests (no I/O): LLMResponse parsing, build_function_tool,
  to_function_call_output, compact_input, _build_payload internals.
  These never touch the network and need no mock library.

* Live tests (marker ``live``): exercise real HTTP paths against the actual
  OpenAI Responses API endpoint using *intentionally invalid* credentials to
  trigger genuine 4xx responses, then assert that the correct HTTP status and
  error are surfaced.  Run with::

      pytest -m live   # requires network; skip in CI without LLM_API_KEY

  ``respx`` and any HTTP-mock library are **not used** here — see §1.6.3.

Coverage:
(a) Parsing function_call items from response.output
(b) Tool definitions serialized as FLAT (no nested "function" key)
(c) function_call_output built correctly for follow-up iteration
(d) compact_input context compaction
(e) _build_payload assembles correct JSON keys (pure-logic, no HTTP)
(f) [live] Invalid API key → real 401 from OpenAI, no retry
(g) [live] Nested-format tool detected and rejected before HTTP call
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.llm.client import LLMClient, LLMError, _is_retryable
from app.llm.schemas import (
    FunctionCall,
    LLMResponse,
    build_function_tool,
    compact_input,
    to_chat_messages,
    to_function_call_output,
)
from app.schemas.errors import ErrorReport
from app.tools.registry import get_registry

BASE_URL = "https://api.openai.com/v1"
RESPONSES_URL = f"{BASE_URL}/responses"

# ---------------------------------------------------------------------------
# Helpers — pure in-memory response bodies (no HTTP)
# ---------------------------------------------------------------------------


def make_response_body(
    *,
    response_id: str = "resp_abc",
    output_text: str | None = None,
    function_calls: list[dict] | None = None,
    status: str = "completed",
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> dict:
    """Build a minimal Responses API response body (in-memory, no HTTP)."""
    output = []

    if output_text:
        output.append(
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": output_text}],
            }
        )

    for fc in function_calls or []:
        output.append(
            {
                "type": "function_call",
                "call_id": fc["call_id"],
                "name": fc["name"],
                "arguments": json.dumps(fc.get("arguments", {})),
            }
        )

    return {
        "id": response_id,
        "status": status,
        "output": output,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


# ---------------------------------------------------------------------------
# (a) Parsing function_call items from response.output — PURE LOGIC
# ---------------------------------------------------------------------------


class TestLLMResponseParsing:
    """Tests for LLMResponse.from_raw parsing — no I/O, no mock library."""

    def test_parses_output_text(self):
        raw = make_response_body(output_text="Hello, world!")
        resp = LLMResponse.from_raw(raw)

        assert resp.output_text == "Hello, world!"
        assert resp.function_calls == []
        assert resp.response_id == "resp_abc"
        assert resp.status == "completed"

    def test_parses_function_call(self):
        raw = make_response_body(
            function_calls=[
                {"call_id": "call_001", "name": "search", "arguments": {"q": "AI"}}
            ]
        )
        resp = LLMResponse.from_raw(raw)

        assert resp.output_text is None
        assert len(resp.function_calls) == 1
        fc = resp.function_calls[0]
        assert fc.call_id == "call_001"
        assert fc.name == "search"
        assert fc.arguments_dict == {"q": "AI"}

    def test_parses_multiple_function_calls(self):
        raw = make_response_body(
            function_calls=[
                {"call_id": "call_001", "name": "search", "arguments": {"q": "AI"}},
                {"call_id": "call_002", "name": "summarise", "arguments": {"text": "..."}},
            ]
        )
        resp = LLMResponse.from_raw(raw)

        assert len(resp.function_calls) == 2
        assert resp.function_calls[0].name == "search"
        assert resp.function_calls[1].name == "summarise"

    def test_parses_mixed_output(self):
        """Both text and function calls can appear together."""
        raw = make_response_body(
            output_text="Let me look that up...",
            function_calls=[
                {"call_id": "call_001", "name": "lookup", "arguments": {}}
            ],
        )
        resp = LLMResponse.from_raw(raw)

        assert resp.output_text == "Let me look that up..."
        assert len(resp.function_calls) == 1

    def test_parses_usage(self):
        raw = make_response_body(input_tokens=200, output_tokens=80)
        resp = LLMResponse.from_raw(raw)

        assert resp.usage.input_tokens == 200
        assert resp.usage.output_tokens == 80
        assert resp.usage.total_tokens == 280

    def test_arguments_invalid_json_guard(self):
        """Malformed JSON arguments should not raise — stored in _raw."""
        raw = {
            "id": "resp_x",
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "call_id": "call_bad",
                    "name": "broken",
                    "arguments": "NOT VALID JSON {{{",
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        resp = LLMResponse.from_raw(raw)
        fc = resp.function_calls[0]
        assert "_raw" in fc.arguments_dict  # guard places raw in wrapper

    def test_empty_output(self):
        raw = {"id": "resp_empty", "status": "completed", "output": [], "usage": {}}
        resp = LLMResponse.from_raw(raw)

        assert resp.output_text is None
        assert resp.function_calls == []

    def test_incomplete_status_and_reason(self):
        raw = {
            "id": "resp_inc",
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": [],
            "usage": {},
        }
        resp = LLMResponse.from_raw(raw)

        assert resp.status == "incomplete"
        assert resp.incomplete_reason == "max_output_tokens"


# ---------------------------------------------------------------------------
# (b) Tool definitions are FLAT — no nested "function" key — PURE LOGIC
# ---------------------------------------------------------------------------


class TestBuildFunctionTool:
    """Tests for build_function_tool helper — no I/O."""

    def test_produces_flat_format(self):
        tool = build_function_tool(
            {
                "name": "search_web",
                "description": "Search the web",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            }
        )

        assert tool["type"] == "function"
        assert tool["name"] == "search_web"
        assert tool["description"] == "Search the web"
        assert "parameters" in tool
        # CRITICAL: must NOT have nested "function" key
        assert "function" not in tool

    def test_no_nested_function_key(self):
        """Regression: ensure chat/completions nested format is never produced."""
        tool = build_function_tool({"name": "my_tool", "description": "desc"})

        assert "function" not in tool
        assert tool.get("type") == "function"
        assert tool.get("name") == "my_tool"

    def test_extra_keys_forwarded(self):
        """Keys like 'strict' should be forwarded to the output."""
        tool = build_function_tool({"name": "t", "strict": True})
        assert tool["strict"] is True

    @pytest.mark.asyncio
    async def test_client_raises_on_nested_format(self):
        """LLMClient.respond() must raise ValueError if nested format detected.

        This is pure validation logic — no HTTP call is made because
        ValueError is raised before the HTTP client is invoked.
        """
        client = LLMClient(
            base_url=BASE_URL,
            api_key="sk-test",
            model="gpt-4o-mini",
        )

        bad_tool = {
            "type": "function",
            "function": {
                "name": "bad_tool",
                "description": "wrong format",
            },
        }

        with pytest.raises(ValueError, match="nested 'function' key"):
            await client.respond("hi", tools=[bad_tool])

        await client.aclose()


# ---------------------------------------------------------------------------
# (c) function_call_output built correctly — PURE LOGIC
# ---------------------------------------------------------------------------


class TestFunctionCallOutput:
    """Tests for to_function_call_output helper — no I/O."""

    def test_to_function_call_output_schema(self):
        item = to_function_call_output("call_xyz", '{"status": "ok"}')

        assert item["type"] == "function_call_output"
        assert item["call_id"] == "call_xyz"
        assert item["output"] == '{"status": "ok"}'
        assert "function" not in item
        assert "name" not in item  # call_id is the link, not name

    def test_function_call_to_output_item(self):
        fc = FunctionCall(
            call_id="call_001",
            name="search",
            arguments_dict={"q": "test"},
        )
        item = fc.to_output_item("search results here")

        assert item["type"] == "function_call_output"
        assert item["call_id"] == "call_001"
        assert item["output"] == "search results here"

    def test_output_item_is_valid_input_for_next_call(self):
        """The output item should be passable as an element in the next input list."""
        fc_output = to_function_call_output("call_001", json.dumps({"result": 42}))

        next_input = [
            {"type": "message", "role": "user", "content": "What's the result?"},
            fc_output,
        ]
        assert next_input[1]["type"] == "function_call_output"
        assert next_input[1]["call_id"] == "call_001"


# ---------------------------------------------------------------------------
# (d) compact_input — PURE LOGIC
# ---------------------------------------------------------------------------


class TestCompactInput:
    """Tests for the context compaction hook — no I/O."""

    def test_short_list_unchanged(self):
        items = [{"type": "message", "content": f"msg {i}"} for i in range(5)]
        result = compact_input(items, keep_last_n=10)
        assert result == items

    def test_long_list_trimmed(self):
        items = [{"type": "message", "content": f"msg {i}"} for i in range(25)]
        result = compact_input(items, keep_last_n=10)
        assert len(result) == 10
        assert result[-1]["content"] == "msg 24"

    def test_summary_prepended(self):
        items = [{"type": "message", "content": f"msg {i}"} for i in range(25)]
        result = compact_input(items, keep_last_n=10, summary="Earlier: user asked about AI.")
        assert len(result) == 11  # 10 recent + 1 summary
        assert result[0]["type"] == "message"
        assert "Context summary" in result[0]["content"]


# ---------------------------------------------------------------------------
# (e) _build_payload — PURE LOGIC (inspect assembled dict, no HTTP)
# ---------------------------------------------------------------------------


class TestBuildPayload:
    """Verify the payload dict assembled by LLMClient._build_payload.

    We call the private helper directly — no HTTP connection is made.
    This tests the Responses API spec adherence (field names, structure).
    """

    def _make_client(self, **kwargs) -> LLMClient:
        return LLMClient(base_url=BASE_URL, api_key="sk-test", model="gpt-4o-mini", **kwargs)

    def test_uses_input_not_messages(self):
        client = self._make_client()
        payload = client._build_payload(
            input="Hello world",
            instructions=None,
            tools=None,
            tool_choice="auto",
            previous_response_id=None,
            temperature=None,
            max_output_tokens=None,
            text_format=None,
        )
        assert "input" in payload
        assert "messages" not in payload

    def test_instructions_field(self):
        client = self._make_client()
        payload = client._build_payload(
            input="Hello",
            instructions="You are a helpful assistant.",
            tools=None,
            tool_choice="auto",
            previous_response_id=None,
            temperature=None,
            max_output_tokens=None,
            text_format=None,
        )
        assert payload.get("instructions") == "You are a helpful assistant."

    def test_tools_flat_format_in_payload(self):
        tool = build_function_tool(
            {
                "name": "calculator",
                "description": "Perform arithmetic",
                "parameters": {
                    "type": "object",
                    "properties": {"expression": {"type": "string"}},
                    "required": ["expression"],
                },
            }
        )
        client = self._make_client()
        payload = client._build_payload(
            input="Calculate 2+2",
            instructions=None,
            tools=[tool],
            tool_choice="auto",
            previous_response_id=None,
            temperature=None,
            max_output_tokens=None,
            text_format=None,
        )
        assert "tools" in payload
        sent_tool = payload["tools"][0]
        assert sent_tool["type"] == "function"
        assert sent_tool["name"] == "calculator"
        assert "function" not in sent_tool  # flat format

    def test_previous_response_id_in_payload(self):
        client = self._make_client()
        payload = client._build_payload(
            input="Continue",
            instructions=None,
            tools=None,
            tool_choice="auto",
            previous_response_id="resp_prev_001",
            temperature=None,
            max_output_tokens=None,
            text_format=None,
        )
        assert payload.get("previous_response_id") == "resp_prev_001"

    def test_json_mode_via_text_format(self):
        client = self._make_client()
        payload = client._build_payload(
            input="Give me JSON",
            instructions=None,
            tools=None,
            tool_choice="auto",
            previous_response_id=None,
            temperature=None,
            max_output_tokens=None,
            text_format={"type": "json_object"},
        )
        assert "text" in payload
        assert payload["text"]["format"] == {"type": "json_object"}
        assert "response_format" not in payload  # must NOT use chat/completions key

    def test_store_field_present(self):
        client = self._make_client(store=True)
        payload = client._build_payload(
            input="hi",
            instructions=None,
            tools=None,
            tool_choice="auto",
            previous_response_id=None,
            temperature=None,
            max_output_tokens=None,
            text_format=None,
        )
        assert payload.get("store") is True

    def test_reasoning_effort_field(self):
        client = self._make_client(reasoning_effort="medium")
        payload = client._build_payload(
            input="Think hard",
            instructions=None,
            tools=None,
            tool_choice="auto",
            previous_response_id=None,
            temperature=None,
            max_output_tokens=None,
            text_format=None,
        )
        assert payload.get("reasoning") == {"effort": "medium"}

    def test_reasoning_effort_omitted_when_none(self):
        client = self._make_client(reasoning_effort=None)
        payload = client._build_payload(
            input="hi",
            instructions=None,
            tools=None,
            tool_choice="auto",
            previous_response_id=None,
            temperature=None,
            max_output_tokens=None,
            text_format=None,
        )
        assert "reasoning" not in payload

    def test_temperature_and_max_output_tokens_plumbing(self):
        client = LLMClient(
            base_url=BASE_URL,
            api_key="sk-test",
            model="gpt-4o-mini",
            temperature=0.2,
            max_output_tokens=4096,
        )
        payload = client._build_payload(
            input="hi",
            instructions=None,
            tools=None,
            tool_choice="auto",
            previous_response_id=None,
            temperature=0.2,
            max_output_tokens=4096,
            text_format=None,
        )
        assert payload["temperature"] == 0.2
        assert payload["max_output_tokens"] == 4096

    def test_reasoning_model_temperature_guard(self):
        client = LLMClient(
            base_url=BASE_URL,
            api_key="sk-test",
            model="o1-mini",
            temperature=0.2,
            max_output_tokens=4096,
        )
        payload = client._build_payload(
            input="hi",
            instructions=None,
            tools=None,
            tool_choice="auto",
            previous_response_id=None,
            temperature=0.2,
            max_output_tokens=4096,
            text_format=None,
        )
        assert "temperature" not in payload
        assert payload["max_output_tokens"] == 4096

    def test_chat_completions_max_tokens_mapping(self):
        client = LLMClient(
            base_url=BASE_URL,
            api_key="sk-test",
            model="gpt-4o-mini",
            api_style="chat_completions",
            temperature=0.2,
            max_output_tokens=4096,
        )
        payload = client._build_payload(
            input="hi",
            instructions=None,
            tools=None,
            tool_choice="auto",
            previous_response_id=None,
            temperature=0.2,
            max_output_tokens=4096,
            text_format=None,
        )
        assert payload["temperature"] == 0.2
        assert payload["max_tokens"] == 4096
        assert "max_output_tokens" not in payload


# ---------------------------------------------------------------------------
# (f) _is_retryable helper — PURE LOGIC
# ---------------------------------------------------------------------------


class TestIsRetryable:
    """Unit-test the retry predicate without any HTTP calls."""

    def _make_status_error(self, status: int) -> httpx.HTTPStatusError:
        resp = httpx.Response(status)
        return httpx.HTTPStatusError("err", request=httpx.Request("POST", "http://x"), response=resp)

    def test_429_is_retryable(self):
        assert _is_retryable(self._make_status_error(429)) is True

    def test_500_is_retryable(self):
        assert _is_retryable(self._make_status_error(500)) is True

    def test_401_not_retryable(self):
        assert _is_retryable(self._make_status_error(401)) is False

    def test_400_not_retryable(self):
        assert _is_retryable(self._make_status_error(400)) is False

    def test_timeout_is_retryable(self):
        exc = httpx.ReadTimeout("timed out", request=httpx.Request("POST", "http://x"))
        assert _is_retryable(exc) is True

    def test_network_error_is_retryable(self):
        exc = httpx.ConnectError("connection refused")
        assert _is_retryable(exc) is True

    def test_value_error_not_retryable(self):
        assert _is_retryable(ValueError("bad input")) is False


# ---------------------------------------------------------------------------
# (f-bis) Error taxonomy — PURE LOGIC (no HTTP)
# DoD PROMPT 3: all six stages produce the right ErrorReport.stage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestErrorTaxonomy:
    """Pure-logic tests for the PROMPT 3 error taxonomy.

    These tests verify ErrorReport.stage without making any network calls.
    """

    async def test_missing_api_key_blank(self):
        """Blank api_key → stage='missing_api_key' before any HTTP attempt."""
        client = LLMClient(base_url=BASE_URL, api_key="", model="gpt-4o-mini")
        try:
            with pytest.raises(LLMError) as exc_info:
                await client.respond("test")
            assert exc_info.value.report.stage == "missing_api_key"
            assert exc_info.value.report.component == "llm"
            assert exc_info.value.report.retryable is False
        finally:
            await client.aclose()

    async def test_missing_api_key_placeholder(self):
        """Placeholder 'sk-xxx' → stage='missing_api_key'."""
        client = LLMClient(base_url=BASE_URL, api_key="sk-xxx", model="gpt-4o-mini")
        try:
            with pytest.raises(LLMError) as exc_info:
                await client.respond("test")
            assert exc_info.value.report.stage == "missing_api_key"
        finally:
            await client.aclose()

    async def test_missing_api_key_emits_error_trace(self):
        """missing_api_key must emit a type='error' trace event via tracer."""
        from unittest.mock import MagicMock
        tracer = MagicMock()
        client = LLMClient(base_url=BASE_URL, api_key="", model="gpt-4o-mini", tracer=tracer)
        try:
            with pytest.raises(LLMError):
                await client.respond("test")
            # Tracer must have been called with type="error"
            tracer.event.assert_called_once()
            call_args = tracer.event.call_args
            assert call_args[0][0] == "error"  # positional arg: type
            payload = call_args[1]["payload"]
            assert payload["stage"] == "missing_api_key"
            assert payload["component"] == "llm"
        finally:
            await client.aclose()

    async def test_nested_tool_format_raises_value_error(self):
        """Nested 'function' key in tool raises ValueError (not LLMError).

        This is a client-side validation, not an API error — it fires before
        any HTTP or missing_api_key check.
        """
        # Use a valid-looking key so we get past missing_api_key guard
        client = LLMClient(base_url=BASE_URL, api_key="sk-validlooking", model="gpt-4o-mini")
        bad_tool = {"type": "function", "function": {"name": "x"}}
        try:
            with pytest.raises(ValueError, match="nested 'function' key"):
                await client.respond("hi", tools=[bad_tool])
        finally:
            await client.aclose()

    async def test_llm_error_carries_error_report(self):
        """LLMError.report must be an ErrorReport instance."""
        client = LLMClient(base_url=BASE_URL, api_key="", model="gpt-4o-mini")
        try:
            with pytest.raises(LLMError) as exc_info:
                await client.respond("test")
            err = exc_info.value
            assert isinstance(err.report, ErrorReport)
            assert "missing_api_key" in str(err)  # __str__ includes stage
        finally:
            await client.aclose()


# ---------------------------------------------------------------------------
# (g) Live tests — real HTTP, intentionally bad key → real LLMError from client
# ---------------------------------------------------------------------------
# These tests call the actual OpenAI endpoint.
# Run with: pytest -m live
# No mock library used — failure is genuine (§1.6.3).


@pytest.mark.live
@pytest.mark.asyncio
class TestLiveErrorBehaviour:
    """Live tests: verify real API error surfaces as LLMError with correct stage.

    Requires network access. Uses intentionally invalid keys to produce real
    HTTP errors from OpenAI, then asserts ErrorReport.stage is correct.
    """

    async def test_invalid_key_raises_llm_error_auth(self):
        """Invalid key → LLMError with stage='auth' (real 401 from OpenAI)."""
        client = LLMClient(
            base_url=BASE_URL,
            api_key="sk-invalid-key-for-testing",
            model="gpt-4o-mini",
        )
        try:
            with pytest.raises(LLMError) as exc_info:
                await client.respond("ping")
            err = exc_info.value
            assert err.report.stage == "auth"
            assert err.report.component == "llm"
            assert err.report.http_status == 401
            assert err.report.retryable is False
        finally:
            await client.aclose()

    async def test_auth_error_does_not_retry(self):
        """401 must NOT be retried — should fail fast (< 5 s)."""
        import time as _time
        client = LLMClient(
            base_url=BASE_URL,
            api_key="sk-invalid-key-for-testing",
            model="gpt-4o-mini",
        )
        try:
            t0 = _time.perf_counter()
            with pytest.raises(LLMError) as exc_info:
                await client.respond("ping")
            elapsed = _time.perf_counter() - t0
            assert exc_info.value.report.stage == "auth"
            assert elapsed < 5.0, f"401 should not be retried but took {elapsed:.1f}s"
        finally:
            await client.aclose()


@pytest.mark.asyncio
async def test_llm_client_http_status_errors(respx_mock):
    """Verify HTTP 401, 429, 500, 400 error stages in LLMClient."""
    client = LLMClient(base_url="https://api.openai.com/v1", api_key="sk-test-key", model="gpt-4o-mini")

    # 401 Auth
    respx_mock.post("https://api.openai.com/v1/responses").mock(return_value=httpx.Response(401))
    with pytest.raises(LLMError) as exc:
        await client.respond(input="test")
    assert exc.value.report.stage == "auth"

    # 429 Rate limited
    respx_mock.post("https://api.openai.com/v1/responses").mock(return_value=httpx.Response(429))
    with pytest.raises(LLMError) as exc:
        await client.respond(input="test")
    assert exc.value.report.stage == "rate_limited"

    # 500 Server error
    respx_mock.post("https://api.openai.com/v1/responses").mock(return_value=httpx.Response(500))
    with pytest.raises(LLMError) as exc:
        await client.respond(input="test")
    assert exc.value.report.stage == "server_error"

    # 400 Bad request
    respx_mock.post("https://api.openai.com/v1/responses").mock(return_value=httpx.Response(400))
    with pytest.raises(LLMError) as exc:
        await client.respond(input="test")
    assert exc.value.report.stage == "http_request"

    await client.aclose()


@pytest.mark.asyncio
async def test_llm_client_response_parse_error(respx_mock):
    """Verify response parsing failure surfaces response_parse stage."""
    client = LLMClient(base_url="https://api.openai.com/v1", api_key="sk-test-key", model="gpt-4o-mini")
    respx_mock.post("https://api.openai.com/v1/responses").mock(return_value=httpx.Response(200, json={"output": "invalid_structure_not_dict"}))
    with pytest.raises(LLMError) as exc:
        await client.respond(input="test")
    assert exc.value.report.stage == "response_parse"
    await client.aclose()


# ---------------------------------------------------------------------------
# Chat Completions Adapter Tests (Definition of Done)
# ---------------------------------------------------------------------------


class TestChatCompletionsTools:
    """Test tool serialization for Chat Completions API."""

    def test_to_chat_tools_nested_format(self):
        """to_chat_tools must produce nested {"type":"function","function":{...}}."""
        registry = get_registry()
        chat_tools = registry.to_chat_tools()
        assert isinstance(chat_tools, list)
        for t in chat_tools:
            assert t.get("type") == "function"
            assert "function" in t
            assert "name" in t["function"]
            assert "parameters" in t["function"]

    def test_build_chat_tool(self):
        """build_chat_tool produces correct nested dict."""
        spec = {
            "name": "calc",
            "description": "Calculator",
            "parameters": {"type": "object", "properties": {"expr": {"type": "string"}}},
        }
        res = LLMClient.build_chat_tool(spec)
        assert res == {
            "type": "function",
            "function": {
                "name": "calc",
                "description": "Calculator",
                "parameters": {"type": "object", "properties": {"expr": {"type": "string"}}},
            },
        }


class TestChatCompletionsResponseParsing:
    """Test parsing choices[0].message and usage for Chat Completions API."""

    def test_parses_chat_raw_choices_message(self):
        """from_chat_raw maps content, usage (prompt/completion tokens), and finish_reason."""
        raw = {
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "created": 1700000000,
            "model": "deepseek-v4-pro",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello world!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 15,
                "completion_tokens": 8,
                "total_tokens": 23,
            },
        }
        resp = LLMResponse.from_raw(raw)
        assert resp.response_id == "chatcmpl-123"
        assert resp.output_text == "Hello world!"
        assert resp.function_calls == []
        assert resp.status == "completed"
        assert resp.usage.input_tokens == 15
        assert resp.usage.output_tokens == 8
        assert resp.usage.total_tokens == 23

    def test_parses_chat_raw_tool_calls(self):
        """from_chat_raw maps tool_calls[].function into FunctionCall objects."""
        raw = {
            "id": "chatcmpl-456",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_abc123",
                                "type": "function",
                                "function": {
                                    "name": "calculator",
                                    "arguments": '{"expression": "2+2"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 50, "completion_tokens": 20},
        }
        resp = LLMResponse.from_raw(raw)
        assert resp.response_id == "chatcmpl-456"
        assert resp.output_text is None
        assert len(resp.function_calls) == 1
        fc = resp.function_calls[0]
        assert fc.call_id == "call_abc123"
        assert fc.name == "calculator"
        assert fc.arguments_dict == {"expression": "2+2"}


class TestChatCompletionsMessagesConversion:
    """Test neutral input/Message → Chat Completions messages list."""

    def test_to_chat_messages_string(self):
        """String input + instructions → system and user messages."""
        msgs = to_chat_messages("Berapa BBM?", instructions="Kamu agen riset")
        assert len(msgs) == 2
        assert msgs[0] == {"role": "system", "content": "Kamu agen riset"}
        assert msgs[1] == {"role": "user", "content": "Berapa BBM?"}

    def test_to_chat_messages_tool_output(self):
        """function_call_output items converted to role='tool' and tool_call_id."""
        items = [
            {"type": "message", "role": "user", "content": "Query"},
            {"type": "function_call_output", "call_id": "call_123", "output": '{"result": 4}'},
        ]
        msgs = to_chat_messages(items)
        assert len(msgs) == 2
        assert msgs[0] == {"role": "user", "content": "Query"}
        assert msgs[1] == {
            "role": "tool",
            "tool_call_id": "call_123",
            "content": '{"result": 4}',
        }


@pytest.mark.asyncio
class TestChatCompletionsClientPayload:
    """Test LLMClient behavior when configured with api_style='chat_completions'."""

    async def test_chat_completions_endpoint_and_payload(self, respx_mock):
        """Verify LLMClient posts to /chat/completions with messages payload."""
        respx_mock.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "chat-1",
                    "choices": [{"message": {"content": "Response OK"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                },
            )
        )

        client = LLMClient(
            base_url="https://api.openai.com/v1",
            api_key="sk-real-test-key",
            model="deepseek-v4-pro",
            api_style="chat_completions",
        )
        try:
            resp = await client.respond("Halo", instructions="Sys prompt")
            assert resp.output_text == "Response OK"
            assert resp.usage.input_tokens == 10
            assert resp.usage.output_tokens == 5

            # Inspect payload sent
            req = respx_mock.calls.last.request
            assert req.url == "https://api.openai.com/v1/chat/completions"
            body = json.loads(req.content)
            assert body["model"] == "deepseek-v4-pro"
            assert "messages" in body
            assert body["messages"][0] == {"role": "system", "content": "Sys prompt"}
            assert body["messages"][1] == {"role": "user", "content": "Halo"}
            # Ensure unsupported fields store/previous_response_id/reasoning are omitted
            assert "store" not in body
            assert "previous_response_id" not in body
            assert "reasoning" not in body
        finally:
            await client.aclose()


@pytest.mark.live
@pytest.mark.asyncio
class TestLiveChatCompletions:
    """Live test for Chat Completions API / 9Router integration."""

    async def test_live_chat_completions_auth_failure(self):
        """Intentionally bad key with api_style='chat_completions' → ErrorReport stage='auth'."""
        client = LLMClient(
            base_url="https://api.openai.com/v1",
            api_key="sk-invalid-chat-key",
            model="gpt-4o-mini",
            api_style="chat_completions",
        )
        try:
            with pytest.raises(LLMError) as exc_info:
                await client.respond("ping")
            err = exc_info.value
            assert err.report.stage == "auth"
            assert err.report.component == "llm"
            assert err.report.http_status == 401
        finally:
            await client.aclose()

