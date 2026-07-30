"""LLM request/response schemas for the OpenAI Responses API.

These schemas are intentionally different from chat/completions schemas.
The Responses API uses a flat tool definition format and returns output items
rather than message choices.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Tool definition (FLAT — no nested "function" key)
# ---------------------------------------------------------------------------

def build_function_tool(spec: dict[str, Any]) -> dict[str, Any]:
    """Convert a tool spec to the flat format required by the Responses API.

    Responses API tool format (FLAT)::

        {
            "type": "function",
            "name": "my_tool",
            "description": "...",
            "parameters": { ...JSON Schema... }
        }

    This is intentionally different from chat/completions, which uses::

        {
            "type": "function",
            "function": {          # ← this nested key does NOT exist in Responses API
                "name": "...",
                ...
            }
        }

    Parameters
    ----------
    spec:
        Dict with at minimum ``name`` and optionally ``description``,
        ``parameters``.  A ``type`` key is added / overridden to ``"function"``.
    """
    tool: dict[str, Any] = {
        "type": "function",
        "name": spec["name"],
    }
    if "description" in spec:
        tool["description"] = spec["description"]
    if "parameters" in spec:
        tool["parameters"] = spec["parameters"]
    # Extra keys (e.g. strict) are forwarded as-is
    for k, v in spec.items():
        if k not in ("type", "name", "description", "parameters"):
            tool[k] = v
    return tool


def to_function_call_output(call_id: str, result_text: str) -> dict[str, Any]:
    """Build a function_call_output input item for the next Responses API call.

    When returning a tool result in a follow-up request use this format::

        {
            "type": "function_call_output",
            "call_id": "<call_id from the model>",
            "output": "<serialised result as string>"
        }
    """
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": result_text,
    }


# ---------------------------------------------------------------------------
# Response data classes
# ---------------------------------------------------------------------------

@dataclass
class UsageInfo:
    """Token usage from a Responses API response."""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @classmethod
    def from_raw(cls, raw: dict[str, Any] | None) -> "UsageInfo":
        if raw is None:
            return cls()
        return cls(
            input_tokens=raw.get("input_tokens", 0),
            output_tokens=raw.get("output_tokens", 0),
        )


@dataclass
class FunctionCall:
    """A single function call emitted by the model."""

    call_id: str
    name: str
    arguments_dict: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_output_item(cls, item: dict[str, Any]) -> "FunctionCall":
        """Parse a function_call output item from response.output."""
        raw_args = item.get("arguments", "{}")
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except (json.JSONDecodeError, TypeError):
            # Guard: keep raw string in a wrapper rather than crashing
            args = {"_raw": raw_args}
        return cls(
            call_id=item["call_id"],
            name=item["name"],
            arguments_dict=args,
        )

    def to_output_item(self, result_text: str) -> dict[str, Any]:
        """Convenience wrapper: produce the function_call_output input item."""
        return to_function_call_output(self.call_id, result_text)


@dataclass
class LLMResponse:
    """Parsed response from the OpenAI Responses API."""

    response_id: str
    output_text: str | None
    function_calls: list[FunctionCall]
    status: str  # e.g. "completed", "incomplete"
    incomplete_reason: str | None
    usage: UsageInfo
    raw: dict[str, Any]

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "LLMResponse":
        """Parse a full Responses API JSON response body."""
        response_id = raw.get("id", "")
        status = raw.get("status", "completed")
        incomplete_reason = raw.get("incomplete_details", {}).get("reason") if raw.get("incomplete_details") else None
        usage = UsageInfo.from_raw(raw.get("usage"))

        output_texts: list[str] = []
        function_calls: list[FunctionCall] = []

        for item in raw.get("output", []):
            item_type = item.get("type")

            if item_type == "message":
                # Extract text from content array
                for content_block in item.get("content", []):
                    if content_block.get("type") == "output_text":
                        output_texts.append(content_block.get("text", ""))
                    elif content_block.get("type") == "text":
                        output_texts.append(content_block.get("text", ""))

            elif item_type == "function_call":
                function_calls.append(FunctionCall.from_output_item(item))

        output_text = "\n".join(output_texts) if output_texts else None

        return cls(
            response_id=response_id,
            output_text=output_text,
            function_calls=function_calls,
            status=status,
            incomplete_reason=incomplete_reason,
            usage=usage,
            raw=raw,
        )


# ---------------------------------------------------------------------------
# Context compaction helper
# ---------------------------------------------------------------------------

def compact_input(
    items: list[dict[str, Any]],
    *,
    keep_last_n: int = 10,
    summary: str | None = None,
) -> list[dict[str, Any]]:
    """Trim an input item list to prevent context overflow.

    When ``store=False`` (no server-side state), the full conversation history
    must be replayed each call.  This helper keeps only the most recent
    *keep_last_n* items and prepends a summary if provided.

    This is a hook/stub: callers may replace it with a more sophisticated
    summarisation strategy (e.g. call the LLM to summarise).

    Parameters
    ----------
    items:
        The full list of input items accumulated so far.
    keep_last_n:
        Number of most-recent items to retain verbatim.
    summary:
        Optional pre-computed summary string to inject as the first item.
    """
    if len(items) <= keep_last_n:
        return list(items)

    trimmed = list(items[-keep_last_n:])

    if summary:
        summary_item: dict[str, Any] = {
            "type": "message",
            "role": "user",
            "content": f"[Context summary — earlier messages omitted]\n{summary}",
        }
        trimmed = [summary_item] + trimmed

    return trimmed
