import os, json
from openai import OpenAI

client = OpenAI(
    base_url="https://9router.fhs.my.id/v1",
    api_key=os.environ["LLM_API_KEY"],
)

tools = [{
    "type": "function",
    "function": {
        "name": "get_post_count",
        "description": "Hitung jumlah post untuk sebuah topik pada rentang hari tertentu",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "kata kunci topik"},
                "days":  {"type": "integer", "description": "rentang hari ke belakang"},
            },
            "required": ["topic", "days"],
        },
    },
}]

resp = client.chat.completions.create(
    model="xkiro/qwen/qwen3.7-max",
    messages=[{"role": "user", "content": "Berapa jumlah post tentang bbm minggu ini?"}],
    tools=tools,
    tool_choice="required",
)

msg = resp.choices[0].message
print("finish_reason :", resp.choices[0].finish_reason)
print("content       :", repr(msg.content))
print("tool_calls    :", msg.tool_calls)

if msg.tool_calls:
    call = msg.tool_calls[0]
    print("\n✅ TOOL CALL TERBACA")
    print("  name      :", call.function.name)
    print("  arguments :", call.function.arguments)
    try:
        args = json.loads(call.function.arguments)
        print("  parsed    :", args)
    except json.JSONDecodeError:
        print("  ⚠️  arguments bukan JSON valid — parser Anda harus tahan ini")
else:
    print("\n❌ TIDAK ADA tool_calls — model menjawab sebagai teks biasa.")
    print("   Ini yang muncul sebagai stage='response_parse' di aplikasi Anda.")