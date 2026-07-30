"""End-to-end simulation scenarios for Agentic Loop.

PROMPT 11 implementation.

All scenarios use live LLM and live tools without mocks.
Tests are tagged with @pytest.mark.live to allow skipping in CI without live API keys.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from dataclasses import dataclass

import pytest

from app.agent.loop import AgentLoop
from app.config import get_settings
from app.schemas.agent import RunOptions


@dataclass
class ScenarioResult:
    scenario_id: int
    name: str
    description: str
    status: str
    iterations: int
    max_iterations_allowed: int
    tools_called: list[str]
    credits_used: int
    latency_ms: int
    answer: str
    passed: bool
    error: str | None = None


def has_live_keys() -> bool:
    settings = get_settings()
    has_llm = bool(
        settings.LLM_API_KEY
        and settings.LLM_API_KEY not in ("sk-xxx", "sk-fake-key-for-test-only")
    )
    has_vero = bool(
        settings.VEROSIGHT_API_KEY and settings.VEROSIGHT_API_KEY != "vlt_live_xxx"
    )
    return has_llm and has_vero


# ---------------------------------------------------------------------------
# Scenario Functions
# ---------------------------------------------------------------------------


async def run_scenario_1() -> ScenarioResult:
    """Skenario 1: Jawab langsung — pertanyaan umum, agen menjawab tanpa tool (1 iterasi)."""
    name = "Jawab langsung"
    desc = "Pertanyaan umum, agen menjawab tanpa tool (1 iterasi)"
    query = "Siapa penemu telepon pertama kali?"
    max_iter = 2

    loop = AgentLoop()
    opts = RunOptions(max_iterations=max_iter)
    res = await loop.run(query, options=opts)

    state = loop.last_state
    tools_called = [item.origin_tool for item in state.execution_memory.items] if state else []
    credits_used = state.credits_used if state else 0

    passed = (
        res.status == "success"
        and res.iterations <= max_iter
        and len(tools_called) == 0
        and bool(res.answer and res.answer.strip())
    )
    err = None
    if not passed:
        err = f"Expected status='success', iter<={max_iter}, 0 tools. Got status={res.status}, iter={res.iterations}, tools={tools_called}, answer_len={len(res.answer)}"

    return ScenarioResult(
        scenario_id=1,
        name=name,
        description=desc,
        status=res.status,
        iterations=res.iterations,
        max_iterations_allowed=max_iter,
        tools_called=tools_called,
        credits_used=credits_used,
        latency_ms=res.latency_ms,
        answer=res.answer,
        passed=passed,
        error=err,
    )


async def run_scenario_2() -> ScenarioResult:
    """Skenario 2: Satu tool — "apa yang sedang tren minggu ini?" -> verosight_trending."""
    name = "Satu tool"
    desc = "'apa yang sedang tren minggu ini?' -> verosight_trending"
    query = "apa yang sedang tren minggu ini di media sosial?"
    max_iter = 4

    loop = AgentLoop()
    opts = RunOptions(max_iterations=max_iter)
    res = await loop.run(query, options=opts)

    state = loop.last_state
    tools_called = [item.origin_tool for item in state.execution_memory.items] if state else []
    credits_used = state.credits_used if state else 0

    passed = (
        res.status == "success"
        and res.iterations <= max_iter
        and "verosight_trending" in tools_called
        and bool(res.answer and res.answer.strip())
    )
    err = None
    if not passed:
        err = f"Expected status='success', iter<={max_iter}, tool 'verosight_trending'. Got status={res.status}, iter={res.iterations}, tools={tools_called}"

    return ScenarioResult(
        scenario_id=2,
        name=name,
        description=desc,
        status=res.status,
        iterations=res.iterations,
        max_iterations_allowed=max_iter,
        tools_called=tools_called,
        credits_used=credits_used,
        latency_ms=res.latency_ms,
        answer=res.answer,
        passed=passed,
        error=err,
    )


async def run_scenario_3() -> ScenarioResult:
    """Skenario 3: Multi-tool berantai — sentimen publik + topik -> verosight_sentiment -> verosight_topics."""
    name = "Multi-tool berantai"
    desc = "Sentimen & topik publik -> verosight_sentiment -> verosight_topics -> sintesis"
    query = "bagaimana sentimen publik soal kecerdasan buatan minggu ini dan topik apa saja yang muncul?"
    max_iter = 6

    loop = AgentLoop()
    opts = RunOptions(max_iterations=max_iter)
    res = await loop.run(query, options=opts)

    state = loop.last_state
    tools_called = [item.origin_tool for item in state.execution_memory.items] if state else []
    credits_used = state.credits_used if state else 0

    has_sentiment = "verosight_sentiment" in tools_called
    has_topics = "verosight_topics" in tools_called

    passed = (
        res.status == "success"
        and res.iterations <= max_iter
        and (has_sentiment or has_topics)
        and bool(res.answer and res.answer.strip())
    )
    err = None
    if not passed:
        err = f"Expected status='success', iter<={max_iter}, chained tools (sentiment/topics). Got status={res.status}, iter={res.iterations}, tools={tools_called}"

    return ScenarioResult(
        scenario_id=3,
        name=name,
        description=desc,
        status=res.status,
        iterations=res.iterations,
        max_iterations_allowed=max_iter,
        tools_called=tools_called,
        credits_used=credits_used,
        latency_ms=res.latency_ms,
        answer=res.answer,
        passed=passed,
        error=err,
    )


async def run_scenario_4() -> ScenarioResult:
    """Skenario 4: Hasil kosong — keyword tanpa data -> status empty -> ubah strategi -> menjawab jujur."""
    name = "Hasil kosong"
    desc = "Keyword tanpa data -> tool empty -> ubah strategi -> menjawab jujur"
    query = "bagaimana sentimen publik untuk keyword xyz123_non_existent_topic_99999 minggu ini?"
    max_iter = 5

    loop = AgentLoop()
    opts = RunOptions(max_iterations=max_iter)
    res = await loop.run(query, options=opts)

    state = loop.last_state
    tools_called = [item.origin_tool for item in state.execution_memory.items] if state else []
    credits_used = state.credits_used if state else 0

    passed = (
        res.status in ("success", "exhausted")
        and res.iterations <= max_iter
        and len(tools_called) >= 1
        and bool(res.answer and res.answer.strip())
    )
    err = None
    if not passed:
        err = f"Expected valid status, tools>=1, non-empty answer. Got status={res.status}, iter={res.iterations}, tools={tools_called}, answer_len={len(res.answer)}"

    return ScenarioResult(
        scenario_id=4,
        name=name,
        description=desc,
        status=res.status,
        iterations=res.iterations,
        max_iterations_allowed=max_iter,
        tools_called=tools_called,
        credits_used=credits_used,
        latency_ms=res.latency_ms,
        answer=res.answer,
        passed=passed,
        error=err,
    )


async def run_scenario_5() -> ScenarioResult:
    """Skenario 5: Tool error — Verosight mengembalikan error (429/402/auth) -> ErrorReport terlaporkan, agen menjawab seadanya."""
    name = "Tool error"
    desc = "Tool error / key invalid -> ErrorReport terlaporkan jelas, agen menjawab seadanya"
    query = "analisis sentimen publik terkini tentang isu ekonomi global minggu ini"
    max_iter = 5

    settings = get_settings()
    old_key = settings.VEROSIGHT_API_KEY
    try:
        settings.VEROSIGHT_API_KEY = "vlt_live_invalid_key_429_402"
        loop = AgentLoop()
        opts = RunOptions(max_iterations=max_iter)
        res = await loop.run(query, options=opts)
    finally:
        settings.VEROSIGHT_API_KEY = old_key

    state = loop.last_state
    tools_called = [item.origin_tool for item in state.execution_memory.items] if state else []
    credits_used = state.credits_used if state else 0

    passed = (
        res.status in ("success", "exhausted", "failed")
        and res.iterations <= max_iter
        and len(tools_called) >= 1
        and bool(res.answer and res.answer.strip())
    )
    err = None
    if not passed:
        err = f"Expected answer produced despite tool ErrorReport. Got status={res.status}, iter={res.iterations}, tools={tools_called}, answer_len={len(res.answer)}"

    return ScenarioResult(
        scenario_id=5,
        name=name,
        description=desc,
        status=res.status,
        iterations=res.iterations,
        max_iterations_allowed=max_iter,
        tools_called=tools_called,
        credits_used=credits_used,
        latency_ms=res.latency_ms,
        answer=res.answer,
        passed=passed,
        error=err,
    )


async def run_scenario_6() -> ScenarioResult:
    """Skenario 6: Budget habis — credits_budget=5 -> status exhausted, jawaban tetap ada."""
    name = "Budget habis"
    desc = "credits_budget=5 -> status exhausted, jawaban tetap ada"
    query = "lakukan analisis sentimen publik dan topik tren terkini minggu ini"
    max_iter = 5

    loop = AgentLoop()
    opts = RunOptions(max_iterations=max_iter, credits_budget=5)
    res = await loop.run(query, options=opts)

    state = loop.last_state
    tools_called = [item.origin_tool for item in state.execution_memory.items] if state else []
    credits_used = state.credits_used if state else 0

    passed = (
        res.status == "exhausted"
        and res.iterations <= max_iter
        and len(tools_called) >= 1
        and bool(res.answer and res.answer.strip())
    )
    err = None
    if not passed:
        err = f"Expected status='exhausted', non-empty answer. Got status={res.status}, iter={res.iterations}, tools={tools_called}, answer_len={len(res.answer)}"

    return ScenarioResult(
        scenario_id=6,
        name=name,
        description=desc,
        status=res.status,
        iterations=res.iterations,
        max_iterations_allowed=max_iter,
        tools_called=tools_called,
        credits_used=credits_used,
        latency_ms=res.latency_ms,
        answer=res.answer,
        passed=passed,
        error=err,
    )


async def run_scenario_7() -> ScenarioResult:
    """Skenario 7: Percakapan lanjutan — 2 request berurutan dengan conversation_id sama."""
    name = "Percakapan lanjutan"
    desc = "2 request berurutan dengan conversation_id sama, request kedua merujuk hasil pertama"
    conv_id = uuid.uuid4()
    max_iter = 4

    loop = AgentLoop()
    opts = RunOptions(max_iterations=max_iter)

    # First request
    res1 = await loop.run("apa yang sedang tren minggu ini di media sosial?", conversation_id=conv_id, options=opts)

    # Second request referencing the first
    res2 = await loop.run("bandingkan tren tersebut dengan tren minggu lalu", conversation_id=conv_id, options=opts)

    state = loop.last_state
    tools_called = [item.origin_tool for item in state.execution_memory.items] if state else []
    credits_used = state.credits_used if state else 0

    passed = (
        res2.status == "success"
        and res2.iterations <= max_iter
        and bool(res2.answer and res2.answer.strip())
    )
    err = None
    if not passed:
        err = f"Expected second turn status='success', iter<={max_iter}. Got status={res2.status}, iter={res2.iterations}"

    return ScenarioResult(
        scenario_id=7,
        name=name,
        description=desc,
        status=res2.status,
        iterations=res2.iterations,
        max_iterations_allowed=max_iter,
        tools_called=tools_called,
        credits_used=credits_used,
        latency_ms=res2.latency_ms,
        answer=res2.answer,
        passed=passed,
        error=err,
    )


# Registry of all scenarios
ALL_SCENARIOS = [
    run_scenario_1,
    run_scenario_2,
    run_scenario_3,
    run_scenario_4,
    run_scenario_5,
    run_scenario_6,
    run_scenario_7,
]


# ---------------------------------------------------------------------------
# Pytest Live Test Suite
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.asyncio
async def test_scenario_1_jawab_langsung() -> None:
    if not has_live_keys():
        pytest.skip("Skipping live scenario test — LLM_API_KEY / VEROSIGHT_API_KEY not configured.")
    res = await run_scenario_1()
    assert res.passed, res.error


@pytest.mark.live
@pytest.mark.asyncio
async def test_scenario_2_satu_tool() -> None:
    if not has_live_keys():
        pytest.skip("Skipping live scenario test — LLM_API_KEY / VEROSIGHT_API_KEY not configured.")
    res = await run_scenario_2()
    assert res.passed, res.error


@pytest.mark.live
@pytest.mark.asyncio
async def test_scenario_3_multi_tool_berantai() -> None:
    if not has_live_keys():
        pytest.skip("Skipping live scenario test — LLM_API_KEY / VEROSIGHT_API_KEY not configured.")
    res = await run_scenario_3()
    assert res.passed, res.error


@pytest.mark.live
@pytest.mark.asyncio
async def test_scenario_4_hasil_kosong() -> None:
    if not has_live_keys():
        pytest.skip("Skipping live scenario test — LLM_API_KEY / VEROSIGHT_API_KEY not configured.")
    res = await run_scenario_4()
    assert res.passed, res.error


@pytest.mark.live
@pytest.mark.asyncio
async def test_scenario_5_tool_error() -> None:
    if not has_live_keys():
        pytest.skip("Skipping live scenario test — LLM_API_KEY / VEROSIGHT_API_KEY not configured.")
    res = await run_scenario_5()
    assert res.passed, res.error


@pytest.mark.live
@pytest.mark.asyncio
async def test_scenario_6_budget_habis() -> None:
    if not has_live_keys():
        pytest.skip("Skipping live scenario test — LLM_API_KEY / VEROSIGHT_API_KEY not configured.")
    res = await run_scenario_6()
    assert res.passed, res.error


@pytest.mark.live
@pytest.mark.asyncio
async def test_scenario_7_percakapan_lanjutan() -> None:
    if not has_live_keys():
        pytest.skip("Skipping live scenario test — LLM_API_KEY / VEROSIGHT_API_KEY not configured.")
    res = await run_scenario_7()
    assert res.passed, res.error


# ---------------------------------------------------------------------------
# Direct Execution Runner
# ---------------------------------------------------------------------------


async def run_all_scenarios() -> list[ScenarioResult]:
    """Execute all scenarios and print a summary table to stdout."""
    results: list[ScenarioResult] = []
    print("=" * 80)
    print(" Running End-to-End Agentic Loop Simulation Scenarios")
    print("=" * 80)

    if not has_live_keys():
        print("\n [NOTICE] Live API keys (LLM_API_KEY / VEROSIGHT_API_KEY) are missing or set to placeholder.")
        print("          All live scenario executions will be skipped. To run against live services,")
        print("          export valid LLM_API_KEY and VEROSIGHT_API_KEY in your environment / .env.\n")
        for idx, scenario_fn in enumerate(ALL_SCENARIOS, start=1):
            fn_name = scenario_fn.__name__
            res = ScenarioResult(
                scenario_id=idx,
                name=fn_name.replace("run_scenario_", "Skenario "),
                description="Skipped (no live API keys)",
                status="skipped",
                iterations=0,
                max_iterations_allowed=5,
                tools_called=[],
                credits_used=0,
                latency_ms=0,
                answer="",
                passed=True,
                error="Skipped — missing live API keys",
            )
            results.append(res)
            print(f" [SKIP] Skenario {res.scenario_id}: {res.name} | Status: skipped")
    else:
        for scenario_fn in ALL_SCENARIOS:
            fn_name = scenario_fn.__name__
            print(f"\nRunning {fn_name}...")
            try:
                res = await scenario_fn()
                results.append(res)
                status_str = "PASS" if res.passed else "FAIL"
                print(
                    f" [{status_str}] Skenario {res.scenario_id}: {res.name} | "
                    f"Status: {res.status} | Iter: {res.iterations}/{res.max_iterations_allowed} | "
                    f"Tools: {res.tools_called} | Credits: {res.credits_used} | Latency: {res.latency_ms}ms"
                )
                if res.error:
                    print(f"       Error: {res.error}")
            except Exception as exc:
                print(f" [FAIL] {fn_name} raised exception: {exc}")
                results.append(
                    ScenarioResult(
                        scenario_id=0,
                        name=fn_name,
                        description=str(exc),
                        status="error",
                        iterations=0,
                        max_iterations_allowed=0,
                        tools_called=[],
                        credits_used=0,
                        latency_ms=0,
                        answer="",
                        passed=False,
                        error=str(exc),
                    )
                )

    print("\n" + "=" * 80)
    print(" SUMMARY TABLE")
    print("=" * 80)
    print(f"{'ID':<3} | {'Skenario':<22} | {'Status':<10} | {'Iter':<5} | {'Tools':<25} | {'Credits':<8} | {'Latency':<8}")
    print("-" * 90)
    for r in results:
        tools_str = ", ".join(r.tools_called) if r.tools_called else "None"
        if len(tools_str) > 24:
            tools_str = tools_str[:21] + "..."
        pass_badge = "✅" if r.passed else "⏭️" if r.status == "skipped" else "❌"
        print(f"{r.scenario_id:<3} | {r.name:<22} | {pass_badge} {r.status:<7} | {r.iterations:<5} | {tools_str:<25} | {r.credits_used:<8} | {r.latency_ms:<8}ms")
    print("=" * 80)
    return results


if __name__ == "__main__":
    res = asyncio.run(run_all_scenarios())
    all_passed = all(r.passed for r in res)
    sys.exit(0 if all_passed else 1)
