"""Tests for modular tool system: BaseTool, ToolRegistry, ToolExecutor, and builtin tools."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import BaseModel, Field

from app.tools.base import BaseTool, ToolResult, ToolSpec, build_schema_from_pydantic
from app.tools.builtin.calculator import calculator
from app.tools.builtin.clock import current_time
from app.tools.builtin.db_query import _validate_sql_query
from app.tools.executor import ToolExecutor
from app.tools.registry import (
    ToolRegistry,
    get_registry,
    register_tool,
    reset_registry,
)


@pytest.fixture(autouse=True)
def _reset_global_registry():
    reset_registry()
    yield
    reset_registry()


# ---------------------------------------------------------------------------
# 1. BaseTool & Pydantic Schema Helper Tests
# ---------------------------------------------------------------------------

class SampleArgs(BaseModel):
    query: str = Field(..., description="Search query string")
    limit: int = Field(default=10, ge=1, le=100)


def test_build_schema_from_pydantic():
    schema = build_schema_from_pydantic(SampleArgs)
    assert schema["type"] == "object"
    assert "query" in schema["properties"]
    assert "limit" in schema["properties"]
    assert schema["required"] == ["query"]


def test_tool_spec_and_result_models():
    spec = ToolSpec(
        name="test_tool",
        description="A test tool",
        parameters={"type": "object"},
        source="local",
        cost_credits=5,
        timeout_s=10.0,
        tags=["test"],
    )
    assert spec.name == "test_tool"
    assert spec.cost_credits == 5

    res = ToolResult(
        tool_name="test_tool",
        status="success",
        data={"items": [1, 2, 3]},
        duration_ms=120,
        credits_used=5,
        truncated=False,
    )
    assert res.status == "success"
    assert res.credits_used == 5


# ---------------------------------------------------------------------------
# 2. Decorator & Registry Tests
# ---------------------------------------------------------------------------

def test_register_tool_decorator():
    registry = ToolRegistry()

    @register_tool(
        name="dummy_tool",
        description="Dummy test tool",
        cost_credits=0,
        tags=["dummy"],
        registry=registry,
    )
    async def dummy_func(text: str, count: int = 1) -> str:
        """My dummy function."""
        return text * count

    assert len(registry) == 1
    spec = registry.get("dummy_tool").spec
    assert spec.name == "dummy_tool"
    assert spec.description == "Dummy test tool"
    assert "text" in spec.parameters["properties"]
    assert "count" in spec.parameters["properties"]


def test_to_responses_tools_flat_format():
    registry = ToolRegistry()

    @register_tool(
        name="weather_tool",
        description="Get current weather",
        registry=registry,
    )
    async def get_weather(location: str) -> str:
        return f"Weather in {location}"

    tools_flat = registry.to_responses_tools()
    assert len(tools_flat) == 1
    tool_def = tools_flat[0]
    assert tool_def["type"] == "function"
    assert tool_def["name"] == "weather_tool"
    assert tool_def["description"] == "Get current weather"
    assert "parameters" in tool_def
    assert "function" not in tool_def  # MUST NOT be nested under "function"


def test_startup_duplicate_tool_name_detection():
    registry = ToolRegistry()

    @register_tool(name="duplicate_tool", registry=registry)
    async def tool_one() -> str:
        return "one"

    class CustomTool(BaseTool):
        @property
        def spec(self) -> ToolSpec:
            return ToolSpec(
                name="duplicate_tool",
                description="Another tool with same name",
                parameters={},
                source="local",
            )

        async def run(self, **kwargs: Any) -> Any:
            return "two"

    with pytest.raises(ValueError, match="Duplicate tool name registered"):
        registry.register(CustomTool())


def test_tools_disabled_config_filter():
    registry = ToolRegistry()

    @register_tool(name="tool_a", registry=registry)
    async def tool_a() -> str:
        return "a"

    @register_tool(name="tool_b", registry=registry)
    async def tool_b() -> str:
        return "b"

    with patch("app.tools.registry.get_settings") as mock_settings:
        mock_settings.return_value.TOOLS_DISABLED = ["tool_a"]
        assert registry.get("tool_a") is None
        assert registry.get("tool_b") is not None
        specs = registry.list_specs()
        names = [s.name for s in specs]
        assert "tool_a" not in names
        assert "tool_b" in names
        responses_names = [t["name"] for t in registry.to_responses_tools()]
        assert "tool_a" not in responses_names
        assert "tool_b" in responses_names
        chat_names = [t["function"]["name"] for t in registry.to_chat_tools()]
        assert "tool_a" not in chat_names
        assert "tool_b" in chat_names
        to_tools_names = [t["function"]["name"] for t in registry.to_tools("chat_completions")]
        assert "tool_a" not in to_tools_names
        assert "tool_b" in to_tools_names


def test_auto_discovery():
    registry = ToolRegistry()
    registry.discover("app.tools.builtin")
    # Builtin tools (calculator, current_time, db_query) should be registered in global registry
    global_reg = get_registry()
    specs = global_reg.list_specs()
    names = [s.name for s in specs]
    assert "calculator" in names
    assert "current_time" in names
    assert "db_query" in names


# ---------------------------------------------------------------------------
# 3. ToolExecutor & Definition of Done Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invalid_arguments_returns_tool_result_error_not_exception():
    registry = ToolRegistry()

    @register_tool(name="typed_tool", registry=registry)
    async def typed_tool(age: int) -> str:
        return f"Age is {age}"

    executor = ToolExecutor(registry=registry)

    # Pass invalid argument (string instead of int, or missing required parameter)
    res = await executor.execute("typed_tool", {"age": "not-an-int"})
    assert res.status == "error"
    assert res.error is not None
    assert "Invalid arguments for tool 'typed_tool'" in res.error

    # Pass missing required parameter
    res2 = await executor.execute("typed_tool", {})
    assert res2.status == "error"
    assert res2.error is not None
    assert "Invalid arguments for tool 'typed_tool'" in res2.error


@pytest.mark.asyncio
async def test_tool_execution_timeout_handled():
    registry = ToolRegistry()

    @register_tool(name="slow_tool", timeout_s=0.1, registry=registry)
    async def slow_tool() -> str:
        await asyncio.sleep(1.0)
        return "finished"

    executor = ToolExecutor(registry=registry)
    res = await executor.execute("slow_tool", {})
    assert res.status == "timeout"
    assert "timed out after 0.1s" in res.error


@pytest.mark.asyncio
async def test_parallel_execution_of_three_tools():
    registry = ToolRegistry()

    @register_tool(name="tool_1", registry=registry)
    async def tool_1(val: int) -> int:
        await asyncio.sleep(0.05)
        return val * 2

    @register_tool(name="tool_2", registry=registry)
    async def tool_2(val: int) -> int:
        await asyncio.sleep(0.05)
        return val + 10

    @register_tool(name="tool_3", registry=registry)
    async def tool_3(val: int) -> int:
        await asyncio.sleep(0.05)
        return val ** 2

    executor = ToolExecutor(registry=registry)
    calls = [
        ("tool_1", {"val": 5}),
        ("tool_2", {"val": 5}),
        ("tool_3", {"val": 5}),
    ]

    results = await executor.execute_parallel(calls)
    assert len(results) == 3
    assert [r.status for r in results] == ["success", "success", "success"]
    assert [r.data for r in results] == [10, 15, 25]


@pytest.mark.asyncio
async def test_empty_result_normalization():
    registry = ToolRegistry()

    @register_tool(name="empty_list_tool", registry=registry)
    async def empty_list_tool() -> list:
        return []

    @register_tool(name="zero_count_tool", registry=registry)
    async def zero_count_tool() -> dict:
        return {"total": 0, "items": []}

    executor = ToolExecutor(registry=registry)

    res1 = await executor.execute("empty_list_tool", {})
    assert res1.status == "empty"

    res2 = await executor.execute("zero_count_tool", {})
    assert res2.status == "empty"


@pytest.mark.asyncio
async def test_result_truncation_for_large_outputs(monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "TOOL_RESULT_PASSTHROUGH", False)

    registry = ToolRegistry()

    @register_tool(name="large_data_tool", registry=registry)
    async def large_data_tool() -> list[str]:
        return [f"item_{i}_" + "x" * 100 for i in range(300)]

    executor = ToolExecutor(registry=registry)
    res = await executor.execute("large_data_tool", {})
    assert res.status == "success"
    assert res.truncated is True
    last_item = res.data[-1]
    assert "[dipotong," in str(last_item)


@pytest.mark.asyncio
async def test_tool_executor_date_normalization():
    class DateArgs(BaseModel):
        query: str = Field(...)
        date_from: str | None = Field(default=None)
        date_to: str | None = Field(default=None)

    registry = ToolRegistry()

    @register_tool(
        name="date_dummy_tool",
        description="Dummy date tool",
        parameters=DateArgs.model_json_schema(),
        registry=registry,
    )
    async def dummy_tool(query: str, date_from: str | None = None, date_to: str | None = None):
        return {"query": query, "date_from": date_from, "date_to": date_to}

    executor = ToolExecutor(registry=registry)

    # 1. Kosong date_from & date_to -> diisi recent window otomatis
    res1 = await executor.execute("date_dummy_tool", {"query": "test"})
    assert res1.status == "success"
    assert "date_from" in res1.data
    assert "date_to" in res1.data
    assert res1.data["date_from"] < res1.data["date_to"]

    # 2. date_to di masa depan -> clamped ke today
    res2 = await executor.execute(
        "date_dummy_tool",
        {"query": "test", "date_from": "2026-01-01", "date_to": "2099-12-31"},
    )
    assert res2.status == "success"
    assert res2.data["date_to"] < "2099-12-31"

    # 3. date_from > date_to -> swap / fixed
    res3 = await executor.execute(
        "date_dummy_tool",
        {"query": "test", "date_from": "2026-05-01", "date_to": "2026-01-01"},
    )
    assert res3.status == "success"
    assert res3.data["date_from"] <= res3.data["date_to"]


# ---------------------------------------------------------------------------
# 4. Builtin Tools Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_builtin_clock_current_time():
    t_str = await current_time("UTC")
    assert isinstance(t_str, str)
    assert "T" in t_str  # ISO 8601 format


@pytest.mark.asyncio
async def test_builtin_calculator_valid_expressions():
    assert await calculator("2 + 2") == 4.0
    assert await calculator("3 * (4 + 5)") == 27.0
    assert await calculator("sqrt(16) + pow(2, 3)") == 12.0
    assert await calculator("sin(0)") == 0.0


@pytest.mark.asyncio
async def test_builtin_calculator_invalid_and_unsafe_expressions():
    with pytest.raises(ValueError):
        await calculator("__import__('os').system('ls')")

    with pytest.raises(ValueError):
        await calculator("2 + ")



def test_builtin_db_query_sql_validation():
    # Valid query
    sql1 = _validate_sql_query("SELECT id, user_query FROM runs WHERE status='success'")
    assert "LIMIT 100" in sql1

    # DDL/DML rejection
    with pytest.raises(ValueError, match="DDL/DML operations are forbidden"):
        _validate_sql_query("DROP TABLE runs")

    with pytest.raises(ValueError, match="DDL/DML operations are forbidden"):
        _validate_sql_query("DELETE FROM messages WHERE id='123'")

    # Table whitelist rejection
    with pytest.raises(ValueError, match="forbidden"):
        _validate_sql_query("SELECT * FROM pg_user")


def test_normalize_platform_arguments():
    executor = ToolExecutor()

    class MockTool:
        spec = ToolSpec(
            name="search_posts",
            description="search posts",
            parameters={"type": "object", "properties": {"keyword": {"type": "str"}, "platform": {"type": "str"}}},
            source="verosight",
        )

    tool = MockTool()

    # Query without platform mention -> platform is stripped
    args1 = {"keyword": "nuklir", "platform": "x"}
    res1 = executor._normalize_platform_arguments(tool, args1, user_query="apakah nuklir sering dibicarakan?")
    assert "platform" not in res1
    assert res1["keyword"] == "nuklir"

    # Query with explicit platform mention ('tiktok') -> platform preserved
    args2 = {"keyword": "nuklir", "platform": "tiktok"}
    res2 = executor._normalize_platform_arguments(tool, args2, user_query="apa yang viral soal nuklir di TikTok?")
    assert res2.get("platform") == "tiktok"

    # Query with explicit 'di x' mention -> platform preserved
    args3 = {"keyword": "nuklir", "platform": "x"}
    res3 = executor._normalize_platform_arguments(tool, args3, user_query="apa rumor nuklir di x?")
    assert res3.get("platform") == "x"

    # Query without user_query -> platform preserved (no strip if query unknown)
    args4 = {"keyword": "nuklir", "platform": "x"}
    res4 = executor._normalize_platform_arguments(tool, args4, user_query=None)
    assert res4.get("platform") == "x"


    # Multi-statement rejection
    with pytest.raises(ValueError, match="Multi-statement"):
        _validate_sql_query("SELECT 1; SELECT 2;")

    # Limit capping
    sql_limit = _validate_sql_query("SELECT * FROM runs LIMIT 500")
    assert "LIMIT 100" in sql_limit


def test_strip_unknown_arguments():
    executor = ToolExecutor()

    class MockToolStrict:
        spec = ToolSpec(
            name="get_keyword_volume",
            description="get volume",
            parameters={"type": "object", "properties": {"keyword": {"type": "string"}, "days": {"type": "integer"}}},
            source="verosight",
        )

    class MockToolFlexible:
        spec = ToolSpec(
            name="flexible_tool",
            description="flexible",
            parameters={"type": "object", "properties": {"a": {"type": "string"}}, "additionalProperties": True},
            source="local",
        )

    tool_strict = MockToolStrict()
    args1 = {"keyword": "IKN", "days": 30, "display_text": "Cek volume IKN 30 hari terakhir"}
    res1 = executor._strip_unknown_arguments(tool_strict, args1)
    assert res1 == {"keyword": "IKN", "days": 30}
    assert "display_text" not in res1

    tool_flexible = MockToolFlexible()
    args2 = {"a": "test", "display_text": "extra"}
    res2 = executor._strip_unknown_arguments(tool_flexible, args2)
    assert res2 == {"a": "test", "display_text": "extra"}

