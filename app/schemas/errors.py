"""Canonical error reporting model — see PROMPT_PLAN.md §1.6 aturan 2.

Every component (LLM, tool_executor, MCP, DB, Verosight, API layer) uses
``ErrorReport`` to describe failures.  Raw exceptions are never surfaced to
callers; instead they are wrapped here so the caller always knows:

* **which component** failed (``component`` field)
* **which stage** inside that component (``stage`` field)
* **what to check** (``message`` field — human-readable, no secrets)
* whether the failure is **retryable**

Secrets (API keys, tokens) must NEVER appear in any ``ErrorReport`` field.

Usage example::

    from app.schemas.errors import ErrorReport

    report = ErrorReport(
        component="llm",
        stage="http_request",
        message="OpenAI returned 401 Unauthorized — check LLM_API_KEY.",
        http_status=401,
        retryable=False,
    )
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ErrorReport(BaseModel):
    """Structured error report emitted by every failing component.

    Parameters
    ----------
    component:
        Which subsystem produced the error.
    stage:
        Fine-grained stage within the component where the failure occurred.
        Examples: ``"auth_token"``, ``"http_request"``, ``"response_parse"``,
        ``"schema_validation"``, ``"connection"``, ``"missing_api_key"``,
        ``"timeout"``, ``"db_query"``, ``"flush_steps"``.
    message:
        Human-readable explanation of what failed and what the operator should
        check.  Must NOT contain secrets (API keys, tokens).
    http_status:
        HTTP status code returned by the remote service, if applicable.
    provider_code:
        Error code from the upstream provider (e.g. Verosight error code string).
    request_id:
        Upstream request/trace ID for cross-referencing provider logs.
    endpoint:
        The URL or endpoint that was called when the error occurred.
    retryable:
        ``True`` if repeating the operation (e.g. after a delay) is likely to
        succeed.  ``False`` for permanent failures (auth, schema, budget).
    """

    component: Literal["llm", "tool_executor", "mcp", "db", "verosight", "api"]
    stage: str = Field(
        ...,
        description=(
            "Fine-grained stage identifier, e.g. 'auth_token', 'http_request', "
            "'response_parse', 'schema_validation', 'connection', 'missing_api_key'."
        ),
    )
    message: str = Field(
        ...,
        description="Human-readable failure explanation. Must not contain secrets.",
    )
    http_status: int | None = None
    provider_code: str | None = None
    request_id: str | None = None
    endpoint: str | None = None
    retryable: bool = False
