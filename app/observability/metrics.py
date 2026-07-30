"""Prometheus metrics for agentic-loop system.

Exposes metrics required by PROMPT 12:
* runs_total{status}
* iterations_histogram
* tool_calls_total{tool,status}
* credits_used_total
* llm_tokens_total
"""
from __future__ import annotations

from prometheus_client import Counter, Histogram

RUNS_TOTAL = Counter(
    "runs_total",
    "Total number of agent runs executed",
    ["status"],
)

ITERATIONS_HISTOGRAM = Histogram(
    "iterations_histogram",
    "Distribution of iterations per agent run",
    buckets=(1, 2, 3, 4, 5, 8, 10, 15, 20),
)

TOOL_CALLS_TOTAL = Counter(
    "tool_calls_total",
    "Total number of tool calls executed",
    ["tool", "status"],
)

CREDITS_USED_TOTAL = Counter(
    "credits_used_total",
    "Total credits consumed by tool executions",
)

LLM_TOKENS_TOTAL = Counter(
    "llm_tokens_total",
    "Total LLM tokens consumed across runs",
)
