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
    """Convert a tool spec to the flat format required by the Responses API."""
    tool: dict[str, Any] = {
        "type": "function",
        "name": spec["name"],
    }
    if "description" in spec:
        tool["description"] = spec["description"]
    if "parameters" in spec:
        tool["parameters"] = spec["parameters"]
    for k, v in spec.items():
        if k not in ("type", "name", "description", "parameters"):
            tool[k] = v
    return tool


def build_chat_tool(spec: dict[str, Any]) -> dict[str, Any]:
    """Convert a tool spec to the nested format required by Chat Completions API.

    Format::

        {
            "type": "function",
            "function": {
                "name": "my_tool",
                "description": "...",
                "parameters": { ...JSON Schema... }
            }
        }
    """
    fn: dict[str, Any] = {"name": spec["name"]}
    if "description" in spec:
        fn["description"] = spec["description"]
    if "parameters" in spec:
        fn["parameters"] = spec["parameters"]
    for k, v in spec.items():
        if k not in ("type", "name", "description", "parameters", "function"):
            fn[k] = v
    return {"type": "function", "function": fn}


def to_function_call_output(call_id: str, result_text: str) -> dict[str, Any]:
    """Build a function_call_output input item for the Responses API."""
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": result_text,
    }


def to_chat_tool_output(call_id: str, result_text: str) -> dict[str, Any]:
    """Build a tool result message item for Chat Completions API."""
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": result_text,
    }


def to_chat_messages(
    input_data: str | list[Any],
    instructions: str | None = None,
) -> list[dict[str, Any]]:
    """Convert neutral input / instructions into OpenAI Chat Completions messages list format."""
    messages: list[dict[str, Any]] = []

    if instructions:
        messages.append({"role": "system", "content": instructions})

    if isinstance(input_data, str):
        if input_data.strip():
            messages.append({"role": "user", "content": input_data})
        return messages

    if isinstance(input_data, list):
        converted: list[dict[str, Any]] = []
        for item in input_data:
            if hasattr(item, "model_dump"):
                converted.append(item.model_dump(exclude_none=True))
            elif isinstance(item, dict):
                converted.append(item)
            elif hasattr(item, "__dict__"):
                converted.append(dict(item.__dict__))
        input_data = converted

    for item in input_data:
        if not isinstance(item, dict):
            continue

        item_type = item.get("type")
        item_role = item.get("role")

        if item_type == "function_call_output" or item_role == "tool":
            call_id = item.get("call_id") or item.get("tool_call_id", "")
            output_content = item.get("output") if "output" in item else item.get("content", "")
            messages.append({
                "role": "tool",
                "tool_call_id": str(call_id),
                "content": str(output_content) if output_content is not None else "",
            })
        elif item_type == "message" or item_role in ("user", "assistant", "system"):
            role = item_role or item.get("role", "user")
            msg_dict: dict[str, Any] = {"role": role}

            content = item.get("content")
            if content is not None:
                msg_dict["content"] = str(content)
            else:
                msg_dict["content"] = None

            raw_tc = item.get("tool_calls")
            if raw_tc:
                tool_calls = []
                for tc in raw_tc:
                    if isinstance(tc, dict):
                        tool_calls.append(tc)
                    elif hasattr(tc, "id") or hasattr(tc, "call_id"):
                        tc_id = getattr(tc, "id", getattr(tc, "call_id", ""))
                        tc_name = getattr(tc, "name", "")
                        tc_args = getattr(tc, "arguments", getattr(tc, "arguments_dict", {}))
                        args_str = json.dumps(tc_args) if isinstance(tc_args, dict) else str(tc_args)
                        tool_calls.append({
                            "id": tc_id,
                            "type": "function",
                            "function": {"name": tc_name, "arguments": args_str},
                        })
                msg_dict["tool_calls"] = tool_calls
            elif "function_calls" in item and item["function_calls"]:
                tool_calls = []
                for fc in item["function_calls"]:
                    fc_name = fc.get("name", "") if isinstance(fc, dict) else getattr(fc, "name", "")
                    fc_id = fc.get("call_id", "") if isinstance(fc, dict) else getattr(fc, "call_id", "")
                    fc_args = fc.get("arguments_dict", {}) if isinstance(fc, dict) else getattr(fc, "arguments_dict", {})
                    args_str = json.dumps(fc_args) if isinstance(fc_args, dict) else str(fc_args)
                    tool_calls.append({
                        "id": fc_id,
                        "type": "function",
                        "function": {"name": fc_name, "arguments": args_str},
                    })
                msg_dict["tool_calls"] = tool_calls

            messages.append(msg_dict)
        elif item_type == "function_call":
            # Assistant tool call request
            call_id = item.get("call_id", "")
            name = item.get("name", "")
            raw_args = item.get("arguments", "{}")
            args_str = json.dumps(raw_args) if isinstance(raw_args, dict) else str(raw_args)
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": args_str},
                }],
            })
        elif "content" in item and "role" in item:
            messages.append({
                "role": item["role"],
                "content": str(item["content"]) if item["content"] is not None else None,
            })

    return messages


# ---------------------------------------------------------------------------
# Response data classes
# ---------------------------------------------------------------------------

@dataclass
class UsageInfo:
    """Token usage from a Responses or Chat Completions API response."""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @classmethod
    def from_raw(cls, raw: dict[str, Any] | None) -> UsageInfo:
        if raw is None:
            return cls()
        inp = raw.get("input_tokens", raw.get("prompt_tokens", 0))
        out = raw.get("output_tokens", raw.get("completion_tokens", 0))
        return cls(input_tokens=inp, output_tokens=out)


@dataclass
class FunctionCall:
    """A single function call emitted by the model."""

    call_id: str
    name: str
    arguments_dict: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_output_item(cls, item: dict[str, Any]) -> FunctionCall:
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

    @classmethod
    def from_chat_tool_call(cls, tool_call: dict[str, Any]) -> FunctionCall:
        """Parse a tool_call item from a Chat Completions choice message."""
        call_id = tool_call.get("id", "")
        fn = tool_call.get("function", {})
        name = fn.get("name", "")
        raw_args = fn.get("arguments", "{}")
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except (json.JSONDecodeError, TypeError):
            args = {"_raw": raw_args}
        return cls(call_id=call_id, name=name, arguments_dict=args)

    def to_output_item(self, result_text: str) -> dict[str, Any]:
        """Convenience wrapper: produce the function_call_output input item."""
        return to_function_call_output(self.call_id, result_text)

    @property
    def id(self) -> str:
        return self.call_id

    @property
    def arguments(self) -> dict[str, Any]:
        return self.arguments_dict


@dataclass
class LLMResponse:
    """Parsed response from OpenAI (Responses API or Chat Completions API)."""

    response_id: str
    output_text: str | None
    function_calls: list[FunctionCall]
    status: str  # e.g. "completed", "incomplete"
    incomplete_reason: str | None
    usage: UsageInfo
    raw: dict[str, Any]

    @property
    def tool_calls(self) -> list[FunctionCall]:
        return self.function_calls

    @property
    def content(self) -> str | None:
        return self.output_text

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> LLMResponse:
        """Parse a full Responses API or Chat Completions API JSON response body."""
        if "choices" in raw:
            return cls.from_chat_raw(raw)

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
                    if content_block.get("type") == "output_text" or content_block.get("type") == "text":
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

    @classmethod
    def from_chat_raw(cls, raw: dict[str, Any]) -> LLMResponse:
        """Parse a Chat Completions API JSON response body (choices[0].message)."""
        response_id = raw.get("id", "")
        usage = UsageInfo.from_raw(raw.get("usage"))
        choices = raw.get("choices", [])
        if not choices:
            return cls(
                response_id=response_id,
                output_text=None,
                function_calls=[],
                status="completed",
                incomplete_reason=None,
                usage=usage,
                raw=raw,
            )

        first_choice = choices[0]
        finish_reason = first_choice.get("finish_reason", "stop")
        msg = first_choice.get("message", {})

        output_text = msg.get("content")
        if output_text is not None and not str(output_text).strip():
            output_text = None

        function_calls: list[FunctionCall] = []
        raw_tool_calls = msg.get("tool_calls", [])
        if raw_tool_calls:
            for tc in raw_tool_calls:
                function_calls.append(FunctionCall.from_chat_tool_call(tc))

        status = "completed" if finish_reason in ("stop", "tool_calls") else finish_reason
        incomplete_reason = finish_reason if finish_reason not in ("stop", "tool_calls") else None

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
