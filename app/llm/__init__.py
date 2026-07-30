"""LLM package — OpenAI Responses API async client.

Public surface
--------------
* :class:`~app.llm.client.LLMClient` — real async HTTP client.
* :class:`~app.llm.schemas.LLMResponse` — parsed API response.
* :class:`~app.llm.schemas.FunctionCall` — single model tool call.
* :func:`~app.llm.schemas.build_function_tool` — build flat tool definition.
* :func:`~app.llm.schemas.to_function_call_output` — build tool result item.
* :func:`~app.llm.schemas.compact_input` — context compaction helper.

Note: There is no mock mode. All calls go to the real OpenAI Responses API.
See PROMPT_PLAN.md §1.6 for the no-mock policy.
"""
from __future__ import annotations

from app.llm.client import LLMClient, LLMError
from app.llm.schemas import (
    FunctionCall,
    LLMResponse,
    UsageInfo,
    build_function_tool,
    compact_input,
    to_function_call_output,
)

__all__ = [
    "LLMClient",
    "LLMError",
    "LLMResponse",
    "FunctionCall",
    "UsageInfo",
    "build_function_tool",
    "to_function_call_output",
    "compact_input",
]
