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