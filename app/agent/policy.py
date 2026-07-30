"""Agent policy, stop conditions, and budget evaluation.

PROMPT 8 implementation.
"""
from __future__ import annotations

from typing import Literal

from app.agent.state import AgentState
from app.schemas.agent import RunOptions


def evaluate_stop_condition(
    state: AgentState,
    elapsed_s: float,
    no_progress_count: int = 0,
    options: RunOptions | None = None,
) -> Literal["exhausted", "timeout", "no_progress"] | None:
    """Evaluate whether the agent loop should stop.

    Returns
    -------
    'exhausted'
        If iteration count >= max_iterations or credits_used >= credits_budget.
    'timeout'
        If elapsed wall-clock time > timeout_s.
    'no_progress'
        If 3 consecutive iterations occurred without new tools or new information.
    None
        If the loop should continue.
    """
    max_iter = options.max_iterations if options else state.max_iterations
    budget = options.credits_budget if options else state.credits_budget
    timeout_s = options.timeout_s if options else 180.0

    # 1. Timeout check
    if elapsed_s > timeout_s:
        return "timeout"

    # 2. Iteration limit check
    if state.iteration >= max_iter:
        return "exhausted"

    # 3. Budget check
    if state.credits_used >= budget:
        return "exhausted"

    # 4. No-progress check
    if no_progress_count >= 3:
        return "no_progress"

    return None
