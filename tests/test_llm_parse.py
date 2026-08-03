from app.llm.client import parse_chat_response   # sesuaikan nama fungsi parser Anda

def make_fake_chat_response(tool_calls=None, content=None):
    """Bangun objek berbentuk respons chat completions — bukan HTTP mock,
    hanya struktur data yang sama dengan yang dikembalikan SDK."""
    return {
        "choices": [{
            "finish_reason": "tool_calls" if tool_calls else "stop",
            "message": {
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls or None,
            },
        }],
        "usage": {"prompt_tokens": 42, "completion_tokens": 8},
    }

def test_parse_tool_call():
    fake = make_fake_chat_response(tool_calls=[{
        "id": "call_abc",
        "type": "function",
        "function": {"name": "get_post_count",
                     "arguments": '{"topic": "bbm", "days": 7}'},
    }])
    r = parse_chat_response(fake)
    assert r.tool_calls, "tool_calls harus terbaca"
    assert r.tool_calls[0].id == "call_abc"
    assert r.tool_calls[0].name == "get_post_count"
    assert r.tool_calls[0].arguments == {"topic": "bbm", "days": 7}
    assert r.content is None
    # usage dinormalkan ke input/output_tokens
    assert r.usage.input_tokens == 42 and r.usage.output_tokens == 8

def test_parse_plain_answer():
    fake = make_fake_chat_response(content="Ada sekitar 128 post.")
    r = parse_chat_response(fake)
    assert not r.tool_calls
    assert "128" in r.content

def test_parse_bad_arguments():
    """Model kadang mengirim arguments JSON tak valid — parser tidak boleh crash."""
    fake = make_fake_chat_response(tool_calls=[{
        "id": "call_x", "type": "function",
        "function": {"name": "get_post_count", "arguments": "{topic: bbm"},  # rusak
    }])
    r = parse_chat_response(fake)   # harus mengembalikan ErrorReport/skip, bukan exception
    assert r is not None


def test_to_chat_messages_multi_tool_grouping():
    """Verify that multiple adjacent function_call items are grouped into a single assistant message."""
    from app.llm.schemas import to_chat_messages

    input_data = [
        {"type": "function_call", "call_id": "call_1", "name": "tool_a", "arguments": {"x": 1}},
        {"type": "function_call", "call_id": "call_2", "name": "tool_b", "arguments": {"y": 2}},
        {"type": "function_call_output", "call_id": "call_1", "output": "res1"},
        {"type": "function_call_output", "call_id": "call_2", "output": "res2"},
    ]

    msgs = to_chat_messages(input_data, instructions="System prompt")
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "assistant"
    assert "tool_calls" in msgs[1]
    assert len(msgs[1]["tool_calls"]) == 2
    assert msgs[1]["tool_calls"][0]["id"] == "call_1"
    assert msgs[1]["tool_calls"][1]["id"] == "call_2"
    assert msgs[2]["role"] == "tool"
    assert msgs[2]["tool_call_id"] == "call_1"
    assert msgs[3]["role"] == "tool"
    assert msgs[3]["tool_call_id"] == "call_2"


def test_compact_input_tool_pair_integrity():
    """Verify compact_input does not orphan tool outputs when trimming history."""
    from app.llm.schemas import compact_input

    items = [
        {"type": "message", "role": "user", "content": "Query"},
        {"type": "function_call", "call_id": "call_1", "name": "tool_a", "arguments": {}},
        {"type": "function_call_output", "call_id": "call_1", "output": "res1"},
        {"type": "function_call", "call_id": "call_2", "name": "tool_b", "arguments": {}},
        {"type": "function_call_output", "call_id": "call_2", "output": "res2"},
    ]

    # Trim to 2 items: without integrity check it would pick items[-2:] which starts with function_call_output call_2.
    # With integrity check, it should include function_call call_2 as well!
    trimmed = compact_input(items, keep_last_n=2)
    assert trimmed[0]["type"] == "function_call"
    assert trimmed[0]["call_id"] == "call_2"
    assert trimmed[1]["type"] == "function_call_output"
    assert trimmed[1]["call_id"] == "call_2"