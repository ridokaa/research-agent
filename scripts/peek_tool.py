# scripts/peek_tool.py
import asyncio, json
from app.tools.registry import ToolRegistry  # sesuaikan import Anda
from app.tools.executor import ToolExecutor   # sesuaikan

async def main():
    reg = ToolRegistry()
    reg.discover()                              # fungsi discover yang baru diperbaiki
    ex = ToolExecutor(reg)
    result = await ex.execute(
        "verosight_search_posts",
        {"query": "bbm", "limit": 10},
        tracer=None,
    )
    print(json.dumps(result.model_dump(), indent=2, default=str))

asyncio.run(main())