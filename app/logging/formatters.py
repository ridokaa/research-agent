"""Pretty-print helpers for trace events.

:func:`render_trace` renders a :class:`~app.schemas.trace.TraceEvent` list as
a human-readable tree, matching the spec in PROMPT 2:

::

    RUN 3f2a… "sentimen publik soal MBG minggu ini"
    ├─ [iter 1] llm_request      model=gpt-4o-mini tools=22 tokens_in=812
    ├─ [iter 1] thinking         "Butuh data sentimen 7 hari terakhir"
    ├─ [iter 1] tool_call        get_sentiment(query="MBG", days=7)      1.24s  5cr
    ├─ [iter 1] tool_result      success total=500 pos=45 neg=12
    └─ [iter 3] final_answer     412 chars | 3 iterasi | 10 credits | 6.1s
"""
from __future__ import annotations

from typing import Any

from app.schemas.trace import TraceEvent

# ---------------------------------------------------------------------------
# Box-drawing characters
# ---------------------------------------------------------------------------
_BRANCH = "├─"
_LAST = "└─"
_INDENT = "   "

# Type → label width (pad to align columns)
_TYPE_WIDTH = 16


# ---------------------------------------------------------------------------
# Per-type summary formatters
# ---------------------------------------------------------------------------

def _fmt_llm_request(p: dict[str, Any]) -> str:
    parts = []
    if model := p.get("model"):
        parts.append(f"model={model}")
    if tools := p.get("tools"):
        parts.append(f"tools={tools}")
    if tok_in := p.get("tokens_in") or p.get("prompt_tokens"):
        parts.append(f"tokens_in={tok_in}")
    return "  ".join(parts)


def _fmt_llm_response(p: dict[str, Any]) -> str:
    parts = []
    if fr := p.get("finish_reason"):
        parts.append(fr)
    if tok_out := p.get("completion_tokens") or p.get("tokens_out"):
        parts.append(f"tokens_out={tok_out}")
    return "  ".join(parts)


def _fmt_thinking(p: dict[str, Any], summary: str) -> str:
    text = p.get("text") or p.get("content") or summary
    # Wrap in quotes, truncate at 80 chars
    if len(text) > 80:
        text = text[:77] + "…"
    return f'"{text}"'


def _fmt_tool_call(p: dict[str, Any], duration_ms: int | None) -> str:
    name = p.get("tool_name") or p.get("name") or "?"
    args_raw = p.get("arguments") or {}
    # Render first few args inline
    arg_str = ", ".join(
        f'{k}="{v}"' if isinstance(v, str) else f"{k}={v}"
        for k, v in list(args_raw.items())[:3]
    )
    call = f"{name}({arg_str})"
    suffix = ""
    if duration_ms is not None:
        suffix += f"  {duration_ms / 1000:.2f}s"
    if credits := p.get("credits_used") or p.get("cost_credits"):
        suffix += f"  {credits}cr"
    return f"{call}{suffix}"


def _fmt_tool_result(p: dict[str, Any]) -> str:
    status = p.get("status", "?")
    # Pull out a few key metrics from the result data
    data = p.get("data") or {}
    extras = []
    if isinstance(data, dict):
        for key in ("total", "pos", "neg", "topics", "count"):
            if key in data:
                extras.append(f"{key}={data[key]}")
        extras = extras[:5]
    extra_str = "  ".join(extras)
    return f"{status}  {extra_str}".rstrip()


def _fmt_final_answer(p: dict[str, Any], summary: str) -> str:
    answer = p.get("answer") or p.get("content") or ""
    chars = len(answer) or p.get("chars", 0)
    iterations = p.get("iterations", "?")
    credits = p.get("credits_used", "?")
    total_s = ""
    if ms := p.get("total_ms") or p.get("latency_ms"):
        total_s = f"  {ms / 1000:.1f}s"
    return f"{chars} chars | {iterations} iterasi | {credits} credits{total_s}"


def _fmt_error(p: dict[str, Any], summary: str) -> str:
    err = p.get("error") or p.get("message") or summary
    if len(err) > 100:
        err = err[:97] + "…"
    return err


def _fmt_generic(p: dict[str, Any], summary: str) -> str:
    """Fallback: show first 3 key=value pairs from payload."""
    if not p:
        return summary if summary else ""
    pairs = [
        f"{k}={v!r}" if isinstance(v, str) else f"{k}={v}"
        for k, v in list(p.items())[:3]
        if not k.startswith("_")
    ]
    return "  ".join(pairs) or summary


# ---------------------------------------------------------------------------
# Main formatter
# ---------------------------------------------------------------------------

def _format_detail(evt: TraceEvent) -> str:
    p = dict(evt.payload)
    t = evt.type
    summary = evt.summary

    if t == "llm_request":
        return _fmt_llm_request(p)
    if t == "llm_response":
        return _fmt_llm_response(p)
    if t in ("thinking", "reflection"):
        return _fmt_thinking(p, summary)
    if t == "tool_call":
        return _fmt_tool_call(p, evt.duration_ms)
    if t == "tool_result":
        return _fmt_tool_result(p)
    if t == "final_answer":
        return _fmt_final_answer(p, summary)
    if t == "error":
        return _fmt_error(p, summary)
    return _fmt_generic(p, summary)


def render_trace(
    events: list[TraceEvent],
    *,
    run_label: str = "",
    query: str = "",
) -> str:
    """Render a list of :class:`TraceEvent` objects as a Unicode tree.

    Parameters
    ----------
    events:
        Ordered list of events (sorted by ``seq`` ascending).
    run_label:
        Short label for the run (e.g. first 8 chars of run_id).
    query:
        The user query, shown on the header line.

    Returns
    -------
    str
        Multi-line string ready to print to a terminal.
    """
    if not events:
        return "(no trace events)"

    lines: list[str] = []

    # --- Header ---
    if not run_label and events:
        run_label = str(events[0].run_id)[:8] + "…"
    q_str = f'"{query}"' if query else ""
    lines.append(f"RUN {run_label} {q_str}".rstrip())

    # --- Body ---
    for idx, evt in enumerate(events):
        is_last = idx == len(events) - 1
        connector = _LAST if is_last else _BRANCH

        type_col = evt.type.ljust(_TYPE_WIDTH)
        detail = _format_detail(evt)

        line = f"{connector} [iter {evt.iteration}] {type_col} {detail}".rstrip()
        lines.append(line)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Rich / coloured variant (used by simulate.py)
# ---------------------------------------------------------------------------

# Colour mapping per event type (ANSI escape codes)
_ANSI: dict[str, str] = {
    "run_start":     "\033[1;36m",   # bold cyan
    "run_end":       "\033[1;36m",
    "context_built": "\033[0;37m",   # grey
    "llm_request":   "\033[0;34m",   # blue
    "llm_response":  "\033[0;34m",
    "thinking":      "\033[0;33m",   # yellow
    "tool_selected": "\033[0;35m",   # magenta
    "tool_call":     "\033[1;35m",   # bold magenta
    "tool_result":   "\033[0;32m",   # green
    "memory_write":  "\033[0;37m",
    "reflection":    "\033[0;33m",
    "final_answer":  "\033[1;32m",   # bold green
    "error":         "\033[1;31m",   # bold red
}
_RESET = "\033[0m"


def render_trace_coloured(
    events: list[TraceEvent],
    *,
    run_label: str = "",
    query: str = "",
) -> str:
    """Like :func:`render_trace` but with ANSI colour codes."""
    if not events:
        return "(no trace events)"

    lines: list[str] = []

    if not run_label and events:
        run_label = str(events[0].run_id)[:8] + "…"
    q_str = f'"{query}"' if query else ""
    lines.append(f"\033[1;37mRUN {run_label} {q_str}\033[0m".rstrip())

    for idx, evt in enumerate(events):
        is_last = idx == len(events) - 1
        connector = _LAST if is_last else _BRANCH

        colour = _ANSI.get(evt.type, "")
        type_col = evt.type.ljust(_TYPE_WIDTH)
        detail = _format_detail(evt)

        line = f"{connector} [iter {evt.iteration}] {colour}{type_col}{_RESET} {detail}".rstrip()
        lines.append(line)

    return "\n".join(lines)
