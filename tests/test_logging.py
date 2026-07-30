"""Unit tests for the logging & tracing system (PROMPT 2).

Tests verify:
1. seq is monotonically increasing and unique.
2. Sensitive keys are redacted to "***".
3. Payloads > 8 KB are truncated and payload["_truncated"] is set.
4. flush() writes all buffered events to the DB (via a mock StepRepo).
5. The .step() context manager measures duration_ms.
6. render_trace() produces the expected tree format.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.logging.formatters import render_trace, render_trace_coloured
from app.logging.tracer import RunTracer, _redact, _truncate_payload
from app.schemas.trace import TraceEvent

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_run_id() -> uuid.UUID:
    return uuid.uuid4()


def make_conversation_id() -> uuid.UUID:
    return uuid.uuid4()


def make_mock_repo() -> MagicMock:
    """Return a mock StepRepo with an awaitable bulk_append_steps."""
    repo = MagicMock()
    repo.bulk_append_steps = AsyncMock(return_value=[])
    return repo


def make_tracer(
    run_id: uuid.UUID | None = None,
    conversation_id: uuid.UUID | None = None,
    repo: Any | None = None,
    overflow_dir: str = "/tmp/test_overflow",
) -> RunTracer:
    return RunTracer(
        run_id=run_id or make_run_id(),
        conversation_id=conversation_id or make_conversation_id(),
        step_repo=repo,
        overflow_dir=overflow_dir,
    )


def make_trace_event(
    *,
    run_id: uuid.UUID | None = None,
    seq: int = 0,
    iteration: int = 0,
    type: str = "thinking",
    summary: str = "test",
    payload: dict | None = None,
    duration_ms: int | None = None,
) -> TraceEvent:
    return TraceEvent(
        run_id=run_id or make_run_id(),
        seq=seq,
        iteration=iteration,
        type=type,  # type: ignore[arg-type]
        summary=summary,
        payload=payload or {},
        duration_ms=duration_ms,
        created_at=datetime.now(tz=UTC),
    )


# ===========================================================================
# 1. Sequence numbering
# ===========================================================================

class TestSeqOrdering:
    def test_seq_starts_at_zero(self) -> None:
        tracer = make_tracer()
        evt = tracer.event("run_start", "start")
        assert evt.seq == 0

    def test_seq_monotonically_increases(self) -> None:
        tracer = make_tracer()
        seqs = [tracer.event("thinking", f"step {i}").seq for i in range(10)]
        assert seqs == list(range(10))

    def test_seq_unique_across_different_types(self) -> None:
        tracer = make_tracer()
        e1 = tracer.event("run_start", "s")
        e2 = tracer.event("llm_request", "r")
        e3 = tracer.event("tool_call", "t")
        assert e1.seq < e2.seq < e3.seq

    def test_iteration_stored_correctly(self) -> None:
        tracer = make_tracer()
        tracer.set_iteration(3)
        evt = tracer.event("thinking", "test")
        assert evt.iteration == 3

    def test_iteration_override(self) -> None:
        tracer = make_tracer()
        tracer.set_iteration(5)
        evt = tracer.event("thinking", "test", iteration=2)
        assert evt.iteration == 2  # explicit override wins


# ===========================================================================
# 2. Redaction of sensitive keys
# ===========================================================================

class TestRedaction:
    @pytest.mark.parametrize("key", [
        "api_key", "apikey", "API_KEY", "ApiKey",
        "authorization", "Authorization", "AUTHORIZATION",
        "token", "access_token", "refresh_token",
        "password", "passwd",
        "secret", "secret_key",
        "private_key",
        "x-api-key",
    ])
    def test_sensitive_key_redacted(self, key: str) -> None:
        payload = {key: "super-secret-value-1234"}
        result = _redact(payload)
        assert result[key] == "***", f"Key {key!r} was not redacted"

    def test_non_sensitive_key_preserved(self) -> None:
        payload = {"model": "gpt-4o-mini", "tokens": 100, "query": "test"}
        result = _redact(payload)
        assert result == payload

    def test_nested_redaction(self) -> None:
        payload = {
            "headers": {"Authorization": "Bearer sk-secret", "Content-Type": "application/json"},
            "body": {"query": "hello", "api_key": "my-key"},
        }
        result = _redact(payload)
        assert result["headers"]["Authorization"] == "***"
        assert result["headers"]["Content-Type"] == "application/json"
        assert result["body"]["api_key"] == "***"
        assert result["body"]["query"] == "hello"

    def test_list_redaction(self) -> None:
        payload = {"items": [{"token": "abc"}, {"name": "safe"}]}
        result = _redact(payload)
        assert result["items"][0]["token"] == "***"
        assert result["items"][1]["name"] == "safe"

    def test_tracer_event_redacts_payload(self) -> None:
        tracer = make_tracer()
        evt = tracer.event(
            "llm_request",
            "calling LLM",
            payload={"api_key": "sk-live-xxx", "model": "gpt-4o"},
        )
        assert evt.payload["api_key"] == "***"
        assert evt.payload["model"] == "gpt-4o"

    def test_non_string_value_not_mangled(self) -> None:
        """Numeric or bool values under sensitive keys are still redacted."""
        payload = {"token": 12345}
        result = _redact(payload)
        assert result["token"] == "***"


# ===========================================================================
# 3. Payload truncation
# ===========================================================================

class TestTruncation:
    def test_small_payload_unchanged(self) -> None:
        run_id = make_run_id()
        payload = {"key": "small value"}
        result = _truncate_payload(payload, overflow_file=None, run_id=run_id, seq=0)
        assert result == payload
        assert "_truncated" not in result

    def test_large_payload_gets_truncated(self) -> None:
        run_id = make_run_id()
        # Build a payload that is > 8 KB
        big_payload = {"data": "x" * 10_000}
        result = _truncate_payload(big_payload, overflow_file=None, run_id=run_id, seq=0)
        assert result.get("_truncated") is True
        assert result.get("_original_bytes", 0) > 8192

    def test_truncated_payload_fits_in_limit(self) -> None:
        run_id = make_run_id()
        big_payload = {"key_" + str(i): "v" * 200 for i in range(100)}
        result = _truncate_payload(big_payload, overflow_file=None, run_id=run_id, seq=0)
        serialised = json.dumps(result).encode()
        assert len(serialised) <= 8 * 1024 + 256  # small tolerance for marker keys

    def test_overflow_written_to_file(self, tmp_path: Any) -> None:
        run_id = make_run_id()
        overflow = tmp_path / "overflow.jsonl"
        big_payload = {"data": "z" * 10_000}
        _truncate_payload(big_payload, overflow_file=overflow, run_id=run_id, seq=7)
        assert overflow.exists()
        line = json.loads(overflow.read_text().strip())
        assert line["seq"] == 7
        assert line["payload"]["data"] == "z" * 10_000

    def test_tracer_event_sets_truncated_marker(self, tmp_path: Any) -> None:
        tracer = make_tracer(overflow_dir=str(tmp_path))
        big = "y" * 10_000
        evt = tracer.event("tool_result", "big result", payload={"data": big})
        assert evt.payload.get("_truncated") is True


# ===========================================================================
# 4. flush() writes all events to the DB
# ===========================================================================

class TestFlush:
    @pytest.mark.asyncio
    async def test_flush_calls_bulk_append(self) -> None:
        repo = make_mock_repo()
        tracer = make_tracer(repo=repo)

        tracer.event("run_start", "start")
        tracer.event("thinking", "thought 1")
        tracer.event("final_answer", "done")

        flushed = await tracer.flush()

        assert flushed == 3
        repo.bulk_append_steps.assert_called_once()
        call_args = repo.bulk_append_steps.call_args[0][0]
        assert len(call_args) == 3

    @pytest.mark.asyncio
    async def test_flush_clears_buffer(self) -> None:
        repo = make_mock_repo()
        tracer = make_tracer(repo=repo)

        tracer.event("run_start", "start")
        await tracer.flush()

        # Buffer should now be empty — second flush does nothing
        flushed2 = await tracer.flush()
        assert flushed2 == 0
        assert repo.bulk_append_steps.call_count == 1  # only called once

    @pytest.mark.asyncio
    async def test_flush_preserves_seq_order(self) -> None:
        repo = make_mock_repo()
        tracer = make_tracer(repo=repo)

        for i in range(5):
            tracer.event("thinking", f"step {i}")

        await tracer.flush()
        steps = repo.bulk_append_steps.call_args[0][0]
        seqs = [s["seq"] for s in steps]
        assert seqs == sorted(seqs)

    @pytest.mark.asyncio
    async def test_flush_no_repo_returns_zero(self) -> None:
        tracer = make_tracer(repo=None)
        tracer.event("thinking", "test")
        result = await tracer.flush()
        assert result == 0

    @pytest.mark.asyncio
    async def test_flush_step_data_correct(self) -> None:
        repo = make_mock_repo()
        run_id = make_run_id()
        tracer = make_tracer(run_id=run_id, repo=repo)

        tracer.set_iteration(2)
        tracer.event("tool_call", "run tool", payload={"tool_name": "search"}, duration_ms=500)

        await tracer.flush()
        steps = repo.bulk_append_steps.call_args[0][0]
        assert steps[0]["run_id"] == run_id
        assert steps[0]["seq"] == 0
        assert steps[0]["iteration"] == 2
        assert steps[0]["type"] == "tool_call"
        assert steps[0]["duration_ms"] == 500

    @pytest.mark.asyncio
    async def test_flush_restores_buffer_on_error(self) -> None:
        repo = make_mock_repo()
        repo.bulk_append_steps.side_effect = RuntimeError("DB down")
        tracer = make_tracer(repo=repo)

        tracer.event("thinking", "test")
        with pytest.raises(RuntimeError):
            await tracer.flush()

        # Buffer should be restored
        assert len(tracer.all_events()) == 1


# ===========================================================================
# 5. .step() context manager
# ===========================================================================

class TestStepContextManager:
    @pytest.mark.asyncio
    async def test_step_emits_event_with_duration(self) -> None:
        tracer = make_tracer()
        async with tracer.step("tool_call", "calling tool") as ctx:
            await asyncio.sleep(0.01)  # 10ms
            ctx.payload = {"tool_name": "clock"}

        events = tracer.all_events()
        assert len(events) == 1
        assert events[0].type == "tool_call"
        assert events[0].duration_ms is not None
        assert events[0].duration_ms >= 10  # at least 10ms
        assert events[0].payload.get("tool_name") == "clock"

    @pytest.mark.asyncio
    async def test_step_emits_even_on_exception(self) -> None:
        tracer = make_tracer()
        with pytest.raises(ValueError):
            async with tracer.step("tool_call", "will fail"):
                raise ValueError("boom")

        events = tracer.all_events()
        assert len(events) == 1
        assert events[0].type == "tool_call"

    @pytest.mark.asyncio
    async def test_step_seq_increments_after_context(self) -> None:
        tracer = make_tracer()
        async with tracer.step("llm_request", "req"):
            pass
        e2 = tracer.event("llm_response", "resp")
        assert e2.seq == 1


# ===========================================================================
# 6. render_trace()
# ===========================================================================

class TestRenderTrace:
    def _make_events(self) -> list[TraceEvent]:
        run_id = uuid.UUID("3f2a0000-0000-0000-0000-000000000000")
        base = datetime(2024, 1, 1, tzinfo=UTC)
        events = [
            TraceEvent(run_id=run_id, seq=0, iteration=1, type="llm_request",
                       summary="LLM call", payload={"model": "gpt-4o-mini", "tools": 22, "tokens_in": 812},
                       duration_ms=None, created_at=base),
            TraceEvent(run_id=run_id, seq=1, iteration=1, type="thinking",
                       summary="Butuh data sentimen 7 hari terakhir", payload={},
                       duration_ms=None, created_at=base),
            TraceEvent(run_id=run_id, seq=2, iteration=1, type="tool_call",
                       summary="get_sentiment call",
                       payload={"tool_name": "get_sentiment", "arguments": {"query": "MBG", "days": 7},
                                "credits_used": 5},
                       duration_ms=1240, created_at=base),
            TraceEvent(run_id=run_id, seq=3, iteration=1, type="tool_result",
                       summary="success",
                       payload={"status": "success", "data": {"total": 500, "pos": 45, "neg": 12}},
                       duration_ms=None, created_at=base),
            TraceEvent(run_id=run_id, seq=4, iteration=3, type="final_answer",
                       summary="done",
                       payload={"answer": "x" * 412, "iterations": 3, "credits_used": 10, "latency_ms": 6100},
                       duration_ms=None, created_at=base),
        ]
        return events

    def test_render_starts_with_run_header(self) -> None:
        events = self._make_events()
        output = render_trace(events, run_label="3f2a…", query="sentimen publik soal MBG minggu ini")
        assert output.startswith("RUN 3f2a… ")
        assert "sentimen publik soal MBG minggu ini" in output

    def test_render_contains_all_events(self) -> None:
        events = self._make_events()
        output = render_trace(events)
        assert "llm_request" in output
        assert "thinking" in output
        assert "tool_call" in output
        assert "tool_result" in output
        assert "final_answer" in output

    def test_render_last_line_uses_corner(self) -> None:
        events = self._make_events()
        lines = render_trace(events).split("\n")
        # Last event line should use └─
        assert lines[-1].startswith("└─")

    def test_render_other_lines_use_branch(self) -> None:
        events = self._make_events()
        lines = render_trace(events).split("\n")
        # All except header and last body line should use ├─
        body = lines[1:-1]
        for line in body:
            assert line.startswith("├─"), f"Expected ├─, got: {line!r}"

    def test_render_tool_call_shows_args(self) -> None:
        events = self._make_events()
        output = render_trace(events)
        assert "get_sentiment" in output
        assert "MBG" in output

    def test_render_final_answer_shows_chars(self) -> None:
        events = self._make_events()
        output = render_trace(events)
        assert "412 chars" in output
        assert "3 iterasi" in output

    def test_render_empty_events(self) -> None:
        output = render_trace([])
        assert "(no trace events)" in output

    def test_render_coloured_contains_ansi(self) -> None:
        events = self._make_events()
        output = render_trace_coloured(events)
        assert "\033[" in output  # ANSI escape present

    def test_render_iter_labels(self) -> None:
        events = self._make_events()
        output = render_trace(events)
        assert "[iter 1]" in output
        assert "[iter 3]" in output

    def test_render_single_event_uses_corner(self) -> None:
        run_id = make_run_id()
        events = [TraceEvent(
            run_id=run_id, seq=0, iteration=0, type="run_start",
            summary="start", payload={}, duration_ms=None,
            created_at=datetime.now(tz=UTC),
        )]
        lines = render_trace(events).split("\n")
        assert lines[1].startswith("└─")


# ===========================================================================
# 7. TraceEvent schema validation
# ===========================================================================

class TestTraceEventSchema:
    def test_valid_event(self) -> None:
        evt = TraceEvent(
            run_id=uuid.uuid4(),
            seq=0,
            iteration=0,
            type="thinking",
            summary="test",
            payload={},
            duration_ms=None,
            created_at=datetime.now(tz=UTC),
        )
        assert evt.seq == 0

    def test_invalid_type_raises(self) -> None:
        with pytest.raises(ValueError):
            TraceEvent(
                run_id=uuid.uuid4(),
                seq=0,
                iteration=0,
                type="invalid_event_type",  # type: ignore[arg-type]
                summary="test",
                payload={},
                duration_ms=None,
                created_at=datetime.now(tz=UTC),
            )

    def test_all_valid_types_accepted(self) -> None:
        valid_types = [
            "run_start", "context_built", "llm_request", "llm_response",
            "thinking", "tool_selected", "tool_call", "tool_result",
            "memory_write", "reflection", "final_answer", "error", "run_end",
        ]
        for t in valid_types:
            evt = TraceEvent(
                run_id=uuid.uuid4(),
                seq=0,
                iteration=0,
                type=t,  # type: ignore[arg-type]
                summary="test",
                payload={},
                duration_ms=None,
                created_at=datetime.now(tz=UTC),
            )
            assert evt.type == t
