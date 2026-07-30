"""Logging package — structlog setup, RunTracer, and pretty formatters."""

from app.logging.formatters import render_trace, render_trace_coloured
from app.logging.setup import (
    bind_run_context,
    clear_run_context,
    configure_logging,
    update_iteration,
)
from app.logging.tracer import RunTracer

__all__ = [
    "configure_logging",
    "bind_run_context",
    "clear_run_context",
    "update_iteration",
    "RunTracer",
    "render_trace",
    "render_trace_coloured",
]
