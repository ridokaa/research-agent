"""structlog configuration for agentic-loop.

Two modes, selected by LOG_FORMAT env var:

* ``json``    — one JSON line per event to stdout + append to LOG_FILE.
                Use this in production / Docker.
* ``console`` — rich-coloured, human-readable output.
                Use this during local simulation (LOG_FORMAT=console).

Every log record automatically carries contextvars:
  run_id, conversation_id, iteration
(set via :func:`bind_run_context` / :func:`clear_run_context`).
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Any

import structlog
from structlog.types import EventDict, WrappedLogger

# ---------------------------------------------------------------------------
# Context-var helpers (re-exported so callers only import from here)
# ---------------------------------------------------------------------------

bind_contextvars = structlog.contextvars.bind_contextvars
clear_contextvars = structlog.contextvars.clear_contextvars
merge_contextvars = structlog.contextvars.merge_contextvars


def bind_run_context(
    *,
    run_id: str,
    conversation_id: str,
    iteration: int = 0,
) -> None:
    """Bind run-scoped context vars so every subsequent log carries them."""
    bind_contextvars(run_id=run_id, conversation_id=conversation_id, iteration=iteration)


def update_iteration(iteration: int) -> None:
    """Update the iteration counter in the current context."""
    bind_contextvars(iteration=iteration)


def clear_run_context() -> None:
    """Remove all run-scoped context vars (call at end of each request)."""
    clear_contextvars()


# ---------------------------------------------------------------------------
# Custom processors
# ---------------------------------------------------------------------------

def _drop_color_message(logger: WrappedLogger, method: str, event_dict: EventDict) -> EventDict:  # noqa: ARG001
    """Remove rich's internal ``color_message`` key before serialisation."""
    event_dict.pop("color_message", None)
    return event_dict


def _add_app_name(logger: WrappedLogger, method: str, event_dict: EventDict) -> EventDict:  # noqa: ARG001
    event_dict.setdefault("app", "agentic-loop")
    return event_dict


# ---------------------------------------------------------------------------
# File handler (JSON-only, appends to LOG_FILE)
# ---------------------------------------------------------------------------

def _build_file_handler(log_file: str) -> logging.Handler:
    """Create a rotating-file handler that writes JSON lines."""
    path = Path(log_file)
    path.parent.mkdir(parents=True, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        path,
        maxBytes=50 * 1024 * 1024,  # 50 MB
        backupCount=5,
        encoding="utf-8",
    )
    # Each line is already a JSON object emitted by orjson / json renderer.
    file_formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            _drop_color_message,
            _add_app_name,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler.setFormatter(file_formatter)
    return handler


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def configure_logging(
    log_level: str = "INFO",
    log_format: str = "console",
    log_file: str = "./logs/agent.jsonl",
) -> None:
    """Configure structlog + stdlib logging for the whole application.

    Call once at startup (e.g. in FastAPI lifespan or ``simulate.py``).

    Parameters
    ----------
    log_level:
        Standard Python level name (``DEBUG``, ``INFO``, …).
    log_format:
        ``"json"`` or ``"console"``.
    log_file:
        Path for the JSONL append file.  Ignored when ``log_format="console"``
        *unless* ``LOG_FILE`` is set explicitly (file handler is always added).
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    # Shared processors applied in both modes --------------------------------
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        _drop_color_message,
        _add_app_name,
    ]

    # --- JSON mode ----------------------------------------------------------
    if log_format == "json":
        stdout_formatter = structlog.stdlib.ProcessorFormatter(
            processors=[
                *shared_processors,
                structlog.processors.dict_tracebacks,
                structlog.processors.JSONRenderer(),
            ],
        )
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setFormatter(stdout_formatter)
        stdout_handler.setLevel(level)

        root_logger = logging.getLogger()
        root_logger.handlers.clear()
        root_logger.addHandler(stdout_handler)
        root_logger.addHandler(_build_file_handler(log_file))
        root_logger.setLevel(level)

        structlog.configure(
            processors=[
                *shared_processors,
                structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
            ],
            wrapper_class=structlog.make_filtering_bound_logger(level),
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )

    # --- Console mode -------------------------------------------------------
    else:
        console_formatter = structlog.stdlib.ProcessorFormatter(
            processors=[
                *shared_processors,
                structlog.dev.ConsoleRenderer(
                    colors=True,
                    exception_formatter=structlog.dev.plain_traceback,
                ),
            ],
        )
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(console_formatter)
        console_handler.setLevel(level)

        # Always attach a JSON file handler even in console mode so that
        # `tail -f logs/agent.jsonl | jq` works during local simulation.
        root_logger = logging.getLogger()
        root_logger.handlers.clear()
        root_logger.addHandler(console_handler)
        root_logger.addHandler(_build_file_handler(log_file))
        root_logger.setLevel(level)

        structlog.configure(
            processors=[
                *shared_processors,
                structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
            ],
            wrapper_class=structlog.make_filtering_bound_logger(level),
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )

    # Silence noisy third-party loggers in non-debug mode
    if log_level.upper() != "DEBUG":
        for noisy in ("httpx", "httpcore", "asyncio", "sqlalchemy.engine"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
