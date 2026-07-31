"""Async LLM client for the OpenAI Responses API.

IMPORTANT: This client targets POST {LLM_BASE_URL}/responses — NOT
/v1/chat/completions.  The wire format is fundamentally different:

* Request uses ``input`` (not ``messages``) + ``instructions`` for system
  context.
* Tool definitions are FLAT: no nested ``"function"`` key.
* The model returns tool invocations as top-level items in ``response.output``
  with ``type="function_call"``.
* Tool results are sent back as ``{"type": "function_call_output", ...}``
  items in the next request's ``input``.
* Server-side state is maintained via ``previous_response_id`` + ``store=true``.
* Usage keys are ``input_tokens`` / ``output_tokens`` (not
  ``prompt_tokens`` / ``completion_tokens``).
* JSON mode is requested via ``text.format = {"type": "json_object"}`` (not
  ``response_format``).

Mixing chat/completions conventions with this endpoint is the most common
cause of "invalid parameter" errors.

Error taxonomy (§1.6 PROMPT_PLAN, PROMPT 3):
    missing_api_key — api_key is blank/placeholder; fail before any HTTP call.
    auth            — HTTP 401/403 from OpenAI.
    rate_limited    — HTTP 429.
    http_request    — network error, timeout, or connection failure.
    response_parse  — 2xx received but response.output structure is unexpected.
    server_error    — HTTP 5xx from OpenAI.
"""
from __future__ import annotations

import time
from typing import Any, Literal

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.llm.schemas import (
    LLMResponse,
    build_chat_tool,
    build_function_tool,
    compact_input,
    to_chat_messages,
    to_chat_tool_output,
)
from app.schemas.errors import ErrorReport

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Retry helpers
# ---------------------------------------------------------------------------

_RETRYABLE_STATUS = {429, 500, 502, 503}
_NO_RETRY_STATUS = {400, 401, 403, 404}

# Timeout and network errors are also retryable
_RETRYABLE_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.NetworkError,
)

# Placeholder value that config.py uses as default — detect it to emit
# missing_api_key rather than trying an HTTP call that will 401.
_PLACEHOLDER_KEY = "sk-xxx"


def _is_retryable(exc: BaseException) -> bool:
    """Decide whether *exc* should trigger a retry."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    return isinstance(exc, _RETRYABLE_EXCEPTIONS)


def _build_retry_decorator() -> Any:
    return retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        reraise=True,
    )


# ---------------------------------------------------------------------------
# LLMClient
# ---------------------------------------------------------------------------


class LLMClient:
    """Async client for OpenAI LLM services (Responses API or Chat Completions API).

    Parameters
    ----------
    base_url:
        Base URL, e.g. ``https://api.openai.com/v1``.
    api_key:
        OpenAI API key (Bearer token).
    model:
        Model identifier, e.g. ``"gpt-4o-mini"`` or ``"deepseek-v4-pro"``.
    timeout:
        HTTP timeout in seconds.
    store:
        If *True*, pass ``store=true`` to the API (Responses API only).
    tracer:
        Optional RunTracer instance.
    reasoning_effort:
        Optional reasoning effort hint ("low" / "medium" / "high").
    api_style:
        API style: ``"responses"`` (POST /responses) or ``"chat_completions"`` (POST /chat/completions).
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        store: bool = True,
        tracer: Any | None = None,
        reasoning_effort: str | None = None,
        api_style: Literal["responses", "chat_completions"] = "responses",
    ) -> None:
        if api_style == "chat_completions":
            self._endpoint = base_url.rstrip("/") + "/chat/completions"
        else:
            self._endpoint = base_url.rstrip("/") + "/responses"

        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._store = store
        self._tracer = tracer
        self._reasoning_effort = reasoning_effort
        self._api_style = api_style

        self._http = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(timeout),
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    async def __aenter__(self) -> LLMClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # Main API
    # ------------------------------------------------------------------

    async def respond(
        self,
        input: str | list[dict[str, Any]],  # noqa: A002
        *,
        instructions: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        previous_response_id: str | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        text_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Call the LLM API (Responses or Chat Completions) and return a parsed :class:`LLMResponse`.

        Raises
        ------
        LLMError
            Wraps an :class:`~app.schemas.errors.ErrorReport` for every failure
            category (see module docstring for the stage taxonomy).
        """
        # ------------------------------------------------------------------
        # Guard: missing or placeholder API key — fail explicitly before HTTP
        # ------------------------------------------------------------------
        if not self._api_key or self._api_key.strip() in ("", _PLACEHOLDER_KEY):
            report = ErrorReport(
                component="llm",
                stage="missing_api_key",
                message=(
                    "LLM_API_KEY is not set or still uses the placeholder value. "
                    "Set a real OpenAI API key in your .env file."
                ),
                endpoint=self._endpoint,
                retryable=False,
            )
            self._emit_error(report, duration_ms=0)
            raise LLMError(report)

        # ------------------------------------------------------------------
        # Context compaction when not using server-side store
        # ------------------------------------------------------------------
        if not self._store and isinstance(input, list) and len(input) > 20:
            input = compact_input(input, keep_last_n=10)  # noqa: A001

        # ------------------------------------------------------------------
        # Tool formatting & validation per API style
        # ------------------------------------------------------------------
        formatted_tools: list[dict[str, Any]] | None = None
        if tools:
            if self._api_style == "responses":
                for t in tools:
                    if "function" in t:
                        raise ValueError(
                            f"Tool '{t.get('name', '?')}' uses nested 'function' key — "
                            "this is the chat/completions format. "
                            "Responses API requires FLAT format: "
                            '{"type":"function","name":"...","description":"...","parameters":{...}}'
                        )
                formatted_tools = tools
            else:
                # Chat completions style: ensure tools are in nested format
                formatted_tools = []
                for t in tools:
                    if "function" in t and "type" in t:
                        formatted_tools.append(t)
                    else:
                        formatted_tools.append(build_chat_tool(t))

        payload = self._build_payload(
            input=input,
            instructions=instructions,
            tools=formatted_tools,
            tool_choice=tool_choice,
            previous_response_id=previous_response_id,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            text_format=text_format,
        )

        t0 = time.perf_counter()

        # Emit llm_request trace event
        if self._tracer is not None:
            self._tracer.event(
                "llm_request",
                f"LLM request — model={self._model}",
                payload={
                    "model": self._model,
                    "tools": len(tools) if tools else 0,
                    "has_previous": previous_response_id is not None,
                    "store": self._store,
                },
            )

        # ------------------------------------------------------------------
        # HTTP call — categorised error handling per PROMPT 3 taxonomy
        # ------------------------------------------------------------------
        try:
            raw = await self._call_with_retry(payload)
        except httpx.HTTPStatusError as exc:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            status = exc.response.status_code
            # Determine fine-grained stage
            if status in (401, 403):
                stage = "auth"
                msg = (
                    f"OpenAI returned HTTP {status} Unauthorized/Forbidden. "
                    "Check that LLM_API_KEY is valid and has the required permissions."
                )
            elif status == 429:
                stage = "rate_limited"
                msg = (
                    "OpenAI rate limit reached (HTTP 429). "
                    "The client will retry automatically; consider increasing LLM_TIMEOUT_S "
                    "or reducing request frequency."
                )
            elif status >= 500:
                stage = "server_error"
                msg = (
                    f"OpenAI returned HTTP {status} server error. "
                    "This is usually transient — the client will retry automatically."
                )
            else:
                stage = "http_request"
                msg = (
                    f"OpenAI returned HTTP {status}. "
                    "Check the request format (400) or endpoint URL."
                )
            report = ErrorReport(
                component="llm",
                stage=stage,
                message=msg,
                http_status=status,
                endpoint=self._endpoint,
                retryable=status in _RETRYABLE_STATUS,
            )
            self._emit_error(report, duration_ms=elapsed_ms)
            raise LLMError(report) from exc

        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            report = ErrorReport(
                component="llm",
                stage="http_request",
                message=(
                    f"Network/timeout error reaching {self._endpoint}: {type(exc).__name__}. "
                    "Check LLM_BASE_URL, LLM_TIMEOUT_S, and network connectivity."
                ),
                endpoint=self._endpoint,
                retryable=True,
            )
            self._emit_error(report, duration_ms=elapsed_ms)
            raise LLMError(report) from exc

        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        # ------------------------------------------------------------------
        # Parse response — surface unexpected structure as response_parse
        # ------------------------------------------------------------------
        try:
            response = LLMResponse.from_raw(raw)
            try:
                from app.observability.metrics import LLM_TOKENS_TOTAL

                LLM_TOKENS_TOTAL.inc(response.usage.total_tokens)
            except Exception:
                pass
        except Exception as exc:  # noqa: BLE001
            report = ErrorReport(
                component="llm",
                stage="response_parse",
                message=(
                    f"Received 2xx from OpenAI but could not parse response.output: "
                    f"{type(exc).__name__}: {exc}. "
                    "The API response format may have changed."
                ),
                endpoint=self._endpoint,
                retryable=False,
            )
            self._emit_error(report, duration_ms=elapsed_ms)
            raise LLMError(report) from exc

        # Emit llm_response trace event with response_id for chain tracking
        if self._tracer is not None:
            self._tracer.event(
                "llm_response",
                f"LLM response — status={response.status}",
                payload={
                    "response_id": response.response_id,
                    "previous_response_id": previous_response_id,
                    "status": response.status,
                    "function_calls": len(response.function_calls),
                    "has_text": response.output_text is not None,
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "duration_ms": elapsed_ms,
                },
                duration_ms=elapsed_ms,
            )

        return response

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _emit_error(self, report: ErrorReport, *, duration_ms: int) -> None:
        """Emit an ``error`` trace event carrying a full ErrorReport payload."""
        if self._tracer is not None:
            self._tracer.event(
                "error",
                f"LLM error — stage={report.stage}",
                payload=report.model_dump(),
                duration_ms=duration_ms,
            )

    def _build_payload(
        self,
        input: str | list[dict[str, Any]],  # noqa: A002
        instructions: str | None,
        tools: list[dict[str, Any]] | None,
        tool_choice: str | dict[str, Any],
        previous_response_id: str | None,
        temperature: float | None,
        max_output_tokens: int | None,
        text_format: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Assemble the JSON payload for the request per _api_style."""
        if self._api_style == "chat_completions":
            messages = to_chat_messages(input, instructions=instructions)
            payload: dict[str, Any] = {
                "model": self._model,
                "messages": messages,
            }
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = tool_choice
            if temperature is not None:
                payload["temperature"] = temperature
            if max_output_tokens is not None:
                payload["max_tokens"] = max_output_tokens
            if text_format is not None:
                payload["response_format"] = {"type": "json_object"}
            return payload

        # Responses API style
        payload: dict[str, Any] = {
            "model": self._model,
            "input": input,
            "store": self._store,
        }

        if instructions is not None:
            payload["instructions"] = instructions

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        if previous_response_id is not None:
            payload["previous_response_id"] = previous_response_id

        if temperature is not None:
            payload["temperature"] = temperature

        if max_output_tokens is not None:
            payload["max_output_tokens"] = max_output_tokens

        if text_format is not None:
            payload["text"] = {"format": text_format}

        if self._reasoning_effort is not None:
            payload["reasoning"] = {"effort": self._reasoning_effort}

        return payload

    async def _call_with_retry(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute the HTTP call with tenacity retry logic."""

        @_build_retry_decorator()
        async def _inner() -> dict[str, Any]:
            resp = await self._http.post(self._endpoint, json=payload)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                if status_code in _NO_RETRY_STATUS:
                    log.warning(
                        "llm.non_retryable_error",
                        status=status_code,
                        body=exc.response.text[:500],
                    )
                raise  # tenacity checks _is_retryable
            return resp.json()

        return await _inner()

    # ------------------------------------------------------------------
    # Convenience builders (re-exported for callers that import from client)
    # ------------------------------------------------------------------

    @staticmethod
    def build_function_tool(spec: dict[str, Any]) -> dict[str, Any]:
        """See :func:`~app.llm.schemas.build_function_tool`."""
        return build_function_tool(spec)

    @staticmethod
    def build_chat_tool(spec: dict[str, Any]) -> dict[str, Any]:
        """See :func:`~app.llm.schemas.build_chat_tool`."""
        return build_chat_tool(spec)

    @staticmethod
    def to_chat_tool_output(call_id: str, result_text: str) -> dict[str, Any]:
        """See :func:`~app.llm.schemas.to_chat_tool_output`."""
        return to_chat_tool_output(call_id, result_text)

    @staticmethod
    def to_chat_messages(
        input_data: str | list[dict[str, Any]], instructions: str | None = None
    ) -> list[dict[str, Any]]:
        """See :func:`~app.llm.schemas.to_chat_messages`."""
        return to_chat_messages(input_data, instructions=instructions)

    @staticmethod
    def compact_input(
        items: list[dict[str, Any]],
        *,
        keep_last_n: int = 10,
        summary: str | None = None,
    ) -> list[dict[str, Any]]:
        """See :func:`~app.llm.schemas.compact_input`."""
        return compact_input(items, keep_last_n=keep_last_n, summary=summary)


# ---------------------------------------------------------------------------
# LLMError — typed exception carrying ErrorReport
# ---------------------------------------------------------------------------


class LLMError(Exception):
    """Raised by :class:`LLMClient` for every categorised failure.

    Carries an :class:`~app.schemas.errors.ErrorReport` so callers can
    inspect ``error.report.stage`` without parsing exception messages.

    Usage::

        try:
            response = await client.respond(...)
        except LLMError as e:
            if e.report.stage == "missing_api_key":
                ...  # operator misconfiguration
            elif e.report.stage == "rate_limited":
                ...  # back off
            ...
    """

    def __init__(self, report: ErrorReport) -> None:
        self.report = report
        super().__init__(f"LLMError[{report.stage}]: {report.message}")
