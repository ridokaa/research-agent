"""RunTracer — emit and persist trace events for one agent run.

Responsibilities
----------------
* Assign monotonically-increasing ``seq`` numbers.
* Write each event to structlog immediately (so ``tail -f`` works).
* Buffer events in memory and :meth:`flush` them to the DB in one batch.
* Redact sensitive keys (api_key, authorization, token, password, secret).
* Truncate payloads > 8 KB; save the full payload to a JSONL sidecar file.
* Provide a context manager :meth:`step` that measures wall-clock duration.

Usage
-----
::

    tracer = RunTracer(run_id, conversation_id, step_repo, overflow_dir="./logs")

    tracer.event("run_start", "Run started", payload={"query": "..."})

    async with tracer.step("llm_request", "Calling LLM") as s:
        response = await llm.chat(...)
        s.payload = {"model": response.model, ...}

    await tracer.flush()            # persist all buffered events to DB
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog

from app.schemas.errors import ErrorReport
from app.schemas.trace import TraceEvent, TraceEventType

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PAYLOAD_MAX_BYTES = 8 * 1024  # 8 KB hard limit for DB storage

# Keys whose *values* will be replaced with "***" before logging / storing.
_REDACT_KEYS: set[str] = {
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "token",
    "access_token",
    "refresh_token",
    "password",
    "passwd",
    "secret",
    "secret_key",
    "private_key",
    "x-api-key",
}

_REDACT_PATTERN = re.compile(
    r"|".join(re.escape(k) for k in _REDACT_KEYS),
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SECRET_VALUE_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9_\-]+"),
    re.compile(r"vlt_[a-zA-Z0-9_\-]+"),
    re.compile(r"(Bearer\s+)[a-zA-Z0-9_\-\.]+"),
]


def _redact(obj: Any, _depth: int = 0) -> Any:
    """Recursively redact sensitive keys and values in a nested structure."""
    if _depth > 10:
        return obj  # safety: prevent infinite recursion on deeply nested data
    if isinstance(obj, str):
        val = obj
        for pat in _SECRET_VALUE_PATTERNS:
            val = pat.sub("[REDACTED]", val)
        return val
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            k_lower = k.lower() if isinstance(k, str) else ""
            is_secret_key = (
                _REDACT_PATTERN.fullmatch(k_lower)
                or "secret" in k_lower
                or (
                    ("token" in k_lower or "auth" in k_lower or "key" in k_lower)
                    and not k_lower.endswith("tokens")
                    and "token_in" not in k_lower
                    and "token_out" not in k_lower
                    and k_lower != "tokens"
                )
            )
            if is_secret_key:
                out[k] = "***"
            else:
                out[k] = _redact(v, _depth + 1)
        return out
    if isinstance(obj, list):
        return [_redact(item, _depth + 1) for item in obj]
    return obj


def _serialize(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")


def _truncate_payload(
    payload: dict[str, Any],
    overflow_file: Path | None,
    run_id: UUID,
    seq: int,
) -> dict[str, Any]:
    """If *payload* exceeds the byte limit, truncate and optionally persist full copy."""
    raw = _serialize(payload)
    if len(raw) <= _PAYLOAD_MAX_BYTES:
        return payload

    # Save full version to JSONL sidecar
    if overflow_file is not None:
        try:
            overflow_file.parent.mkdir(parents=True, exist_ok=True)
            record = {"run_id": str(run_id), "seq": seq, "payload": payload}
            with overflow_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            log.warning("tracer.overflow_write_failed", error=str(exc))

    # Build a trimmed version that fits within the limit
    trimmed: dict[str, Any] = {}
    budget = _PAYLOAD_MAX_BYTES - 64  # reserve space for _truncated marker
    running = 0
    for k, v in payload.items():
        chunk = json.dumps({k: v}, ensure_ascii=False, default=str).encode()
        if running + len(chunk) > budget:
            break
        trimmed[k] = v
        running += len(chunk)
    trimmed["_truncated"] = True
    trimmed["_original_bytes"] = len(raw)
    return trimmed


# ---------------------------------------------------------------------------
# Step context object (mutable carrier for async context manager)
# ---------------------------------------------------------------------------

class _StepContext:
    """Mutable carrier passed into ``async with tracer.step(...) as ctx``."""

    def __init__(self) -> None:
        self.payload: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# RunTracer
# ---------------------------------------------------------------------------

class RunTracer:
    """Trace coordinator for a single agent run.

    Parameters
    ----------
    run_id:
        UUID of the run being traced.
    conversation_id:
        UUID of the parent conversation.
    step_repo:
        A :class:`~app.db.repositories.step.StepRepo` instance (or any object
        with ``bulk_append_steps``).  Pass ``None`` to disable DB persistence
        (useful in unit tests that only care about the event buffer).
    overflow_dir:
        Directory where full-payload JSONL files are written when a payload
        exceeds the 8 KB limit.  Defaults to ``./logs``.
    """

    def __init__(
        self,
        run_id: UUID,
        conversation_id: UUID,
        step_repo: Any | None = None,
        overflow_dir: str | Path = "./logs",
    ) -> None:
        self._run_id = run_id
        self._conversation_id = conversation_id
        self._repo = step_repo
        self._overflow_file = (
            Path(overflow_dir) / f"overflow_{run_id}.jsonl"
        )
        self._seq: int = 0
        self._iteration: int = 0
        self._buffer: list[TraceEvent] = []
        self._lock = asyncio.Lock()
        self._logger = structlog.get_logger(__name__).bind(
            run_id=str(run_id),
            conversation_id=str(conversation_id),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def run_id(self) -> UUID:
        return self._run_id

    @property
    def conversation_id(self) -> UUID:
        return self._conversation_id

    def set_iteration(self, iteration: int) -> None:
        """Update current iteration counter (called by AgentLoop on each loop)."""
        self._iteration = iteration

    def event(
        self,
        type: TraceEventType,  # noqa: A002
        summary: str,
        *,
        payload: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        iteration: int | None = None,
    ) -> TraceEvent:
        """Emit one trace event synchronously.

        Thread-safe *for reads of ``_seq``* but designed to be called from a
        single async task (the agent loop). If you need to call this from
        multiple coroutines simultaneously, use :meth:`async_event`.

        Returns the :class:`~app.schemas.trace.TraceEvent` that was buffered.
        """
        seq = self._seq
        self._seq += 1
        iter_ = iteration if iteration is not None else self._iteration

        # Redact then truncate
        safe_payload = _redact(payload or {})
        stored_payload = _truncate_payload(
            safe_payload, self._overflow_file, self._run_id, seq
        )

        evt = TraceEvent(
            run_id=self._run_id,
            seq=seq,
            iteration=iter_,
            type=type,
            summary=summary,
            payload=stored_payload,
            duration_ms=duration_ms,
            created_at=datetime.now(tz=UTC),
        )

        self._buffer.append(evt)

        # Emit to structlog immediately — this goes to console / JSON file.
        reserved_keys = {"_truncated", "_original_bytes", "iteration", "seq", "event_type", "duration_ms"}
        extra_payload = {k: v for k, v in stored_payload.items() if k not in reserved_keys}
        self._logger.info(
            summary,
            event_type=type,
            seq=seq,
            iteration=iter_,
            duration_ms=duration_ms,
            **extra_payload,
        )

        return evt

    @asynccontextmanager
    async def step(
        self,
        type: TraceEventType,  # noqa: A002
        summary: str,
        *,
        iteration: int | None = None,
    ) -> AsyncGenerator[_StepContext, None]:
        """Async context manager that measures wall-clock duration.

        Usage::

            async with tracer.step("tool_call", "Calling get_sentiment") as ctx:
                result = await tool.run(...)
                ctx.payload = {"status": result.status, ...}
            # → emits event with duration_ms measured automatically
        """
        ctx = _StepContext()
        t0 = time.perf_counter()
        try:
            yield ctx
        finally:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            self.event(
                type,
                summary,
                payload=ctx.payload,
                duration_ms=elapsed_ms,
                iteration=iteration,
            )

    async def flush(self) -> int:
        """Write all buffered events to the database in one batch.

        Returns the number of events flushed. Safe to call multiple times;
        only unflushed events are written.

        This uses :meth:`StepRepo.bulk_append_steps` to avoid N round-trips.
        """
        if self._repo is None or not self._buffer:
            return 0

        async with self._lock:
            batch = list(self._buffer)
            self._buffer.clear()

        steps = [
            {
                "run_id": evt.run_id,
                "seq": evt.seq,
                "iteration": evt.iteration,
                "type": evt.type,
                "summary": evt.summary,
                "payload": dict(evt.payload),
                "duration_ms": evt.duration_ms,
            }
            for evt in batch
        ]

        try:
            await self._repo.bulk_append_steps(steps)
            self._logger.debug("tracer.flushed", count=len(batch))
        except Exception as exc:  # noqa: BLE001
            # Put events back so they aren't lost on transient DB errors.
            async with self._lock:
                self._buffer = batch + self._buffer
            report = ErrorReport(
                component="db",
                stage="flush_steps",
                message=(
                    f"Failed to persist {len(batch)} trace step(s) to DB: {exc}. "
                    "Events returned to buffer and will be retried on next flush."
                ),
                retryable=True,
            )
            self._logger.error(
                "tracer.flush_failed",
                error_report=report.model_dump(),
                count=len(batch),
            )
            raise

        return len(batch)

    def all_events(self) -> list[TraceEvent]:
        """Return a snapshot of all events (flushed + buffered) for inspection."""
        # Note: once flush() drains the buffer we only have what was logged;
        # callers that need the full history should use the DB.
        return list(self._buffer)
