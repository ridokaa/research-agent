import pytest
from app.config import get_settings
from app.llm.client import LLMClient
from app.llm.schemas import (
    build_chat_tool,
    build_function_tool,
    to_function_call_output,
)
from app.schemas import Message

pytestmark = [pytest.mark.live, pytest.mark.asyncio]

TOOL_SPEC = {
    "name": "get_post_count",
    "description": "Hitung jumlah post untuk sebuah topik pada rentang hari",
    "parameters": {
        "type": "object",
        "properties": {
            "topic": {"type": "string"},
            "days":  {"type": "integer"},
        },
        "required": ["topic", "days"],
    },
}


def get_tools_for_style(api_style: str):
    if api_style == "chat_completions":
        return [build_chat_tool(TOOL_SPEC)]
    return [build_function_tool(TOOL_SPEC)]


@pytest.fixture
def client():
    s = get_settings()
    if not s.LLM_API_KEY:
        pytest.skip("LLM_API_KEY tidak diset")
    return LLMClient(
        base_url=s.LLM_BASE_URL,
        api_key=s.LLM_API_KEY,
        model=s.LLM_MODEL,
        api_style=s.LLM_API_STYLE,
        temperature=s.LLM_TEMPERATURE,
        max_output_tokens=s.LLM_MAX_OUTPUT_TOKENS,
    )


async def test_tool_call_diminta(client):
    s = get_settings()
    tools = get_tools_for_style(s.LLM_API_STYLE)
    r = await client.respond(
        input="Berapa post tentang bbm minggu ini?",
        tools=tools,
    )
    assert r.function_calls, "model tidak mengembalikan function_calls"
    assert r.function_calls[0].name == "get_post_count"


async def test_round_trip_penuh(client):
    s = get_settings()
    tools = get_tools_for_style(s.LLM_API_STYLE)
    q = "Berapa post tentang bbm minggu ini?"
    r1 = await client.respond(input=q, tools=tools)
    assert r1.function_calls
    call = r1.function_calls[0]

    if s.LLM_API_STYLE == "responses":
        tool_output_item = to_function_call_output(call.call_id, '{"count": 128}')
        r2 = await client.respond(
            input=[tool_output_item],
            previous_response_id=r1.response_id,
            tools=tools,
        )
    else:
        r2 = await client.respond(
            messages=[
                Message(role="user", content=q),
                Message(role="assistant", tool_calls=[call]),
                Message(role="tool", tool_call_id=call.call_id, content='{"count": 128}'),
            ],
            tools=tools,
        )
    assert r2.output_text and "128" in r2.output_text


async def test_key_salah_lapor_auth():
    from app.config import get_settings
    from app.llm.client import LLMError
    s = get_settings()
    bad = LLMClient(
        base_url=s.LLM_BASE_URL,
        api_key="sk-salah-xxx",
        model=s.LLM_MODEL,
        api_style=s.LLM_API_STYLE,
    )
    with pytest.raises(LLMError) as exc_info:
        await bad.respond(input="hai")
    assert exc_info.value.report.stage == "auth"