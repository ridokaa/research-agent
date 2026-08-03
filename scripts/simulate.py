"""CLI simulation tool for Agentic Loop.

PROMPT 10 & 11 implementation.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from app.agent.loop import AgentLoop
from app.config import get_settings
from app.db.session import close_engine, get_session, init_engine
from app.schemas.agent import RunOptions
from app.tools.registry import init_registry
from scripts.seed_scenarios import ALL_SCENARIOS, run_all_scenarios

app = typer.Typer(help="Agentic Loop local simulation CLI.")
console = Console()


@app.command()
def scenarios(
    max_iter: Annotated[Optional[int], typer.Option("--max-iter", help="Override maximum iterations")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Output results as JSON")] = False,
    live_only: Annotated[bool, typer.Option("--live-only", help="Run live-only scenarios")] = True,
) -> None:
    """Run all simulation scenarios from scripts/seed_scenarios.py."""
    console.print("[bold blue]Executing Agentic Loop Simulation Scenarios...[/bold blue]\n")

    async def _runner():
        init_registry()
        settings = get_settings()
        if settings.VEROSIGHT_ENABLED and settings.VEROSIGHT_MODE in ("mcp", "both"):
            try:
                from app.mcp.manager import get_mcp_manager
                await get_mcp_manager().connect_all()
            except Exception as exc:
                console.print(f"[bold yellow]MCP Connection warning:[/bold yellow] {exc}")
        results = await run_all_scenarios()

        if as_json:
            json_output = [
                {
                    "scenario_id": r.scenario_id,
                    "name": r.name,
                    "status": r.status,
                    "iterations": r.iterations,
                    "max_iterations_allowed": r.max_iterations_allowed,
                    "tools_called": r.tools_called,
                    "credits_used": r.credits_used,
                    "latency_ms": r.latency_ms,
                    "passed": r.passed,
                    "error": r.error,
                }
                for r in results
            ]
            console.print_json(data=json_output)
            return

        table = Table(title="Simulation Scenario Results", show_header=True, header_style="bold magenta")
        table.add_column("ID", style="dim", width=4)
        table.add_column("Skenario", style="bold", width=24)
        table.add_column("Status", width=12)
        table.add_column("Iterasi", justify="right", width=8)
        table.add_column("Tools Called", width=28)
        table.add_column("Credits", justify="right", width=8)
        table.add_column("Latensi", justify="right", width=10)

        for r in results:
            pass_str = "[bold green]PASS[/bold green]" if r.passed else "[bold red]FAIL[/bold red]"
            tools_str = ", ".join(r.tools_called) if r.tools_called else "None"
            table.add_row(
                str(r.scenario_id),
                r.name,
                f"{pass_str} ({r.status})",
                f"{r.iterations}/{r.max_iterations_allowed}",
                tools_str,
                str(r.credits_used),
                f"{r.latency_ms} ms",
            )

        console.print(table)

    asyncio.run(_runner())


@app.command()
def ask(
    query: Annotated[str, typer.Argument(help="The question / query for the agent")],
    max_iter: Annotated[int, typer.Option("--max-iter", help="Maximum allowed iterations")] = 8,
    credits_budget: Annotated[int, typer.Option("--credits-budget", help="Credits budget limit")] = 100,
) -> None:
    """Run agent loop for a query and render colorized terminal trace."""
    console.print(Panel(f"[bold yellow]User Query:[/bold yellow] {query}", title="Agentic Loop Simulation"))

    async def _run_query():
        init_registry()
        settings = get_settings()
        if settings.VEROSIGHT_ENABLED and settings.VEROSIGHT_MODE in ("mcp", "both"):
            try:
                from app.mcp.manager import get_mcp_manager
                await get_mcp_manager().connect_all()
            except Exception as exc:
                console.print(f"[bold yellow]MCP Connection warning:[/bold yellow] {exc}")
        loop = AgentLoop()
        opts = RunOptions(max_iterations=max_iter, credits_budget=credits_budget)
        res = await loop.run(query, options=opts)

        tracer = loop.last_tracer
        if tracer and hasattr(tracer, "_buffer"):
            console.print("\n[bold cyan]--- Real-time Run Trace Events ---[/bold cyan]")
            for ev in tracer._buffer:
                ev_type = getattr(ev, "type", getattr(ev, "event_type", ""))
                tag = f"[{str(ev_type).upper()}]"
                color = "green" if "start" in str(ev_type) or "end" in str(ev_type) else "cyan"
                if "error" in str(ev_type):
                    color = "red"
                elif "tool" in str(ev_type):
                    color = "yellow"
                msg = getattr(ev, "summary", str(getattr(ev, "payload", "")))
                console.print(f"  [bold {color}]{tag:<16}[/bold {color}] {msg}")

        console.print("\n" + "=" * 80)
        console.print(Panel(f"[bold green]Final Answer:[/bold green]\n{res.answer}", title=f"Result (Status: {res.status})"))
        console.print(f"Iterations: {res.iterations} | Latency: {res.latency_ms} ms\n")

    asyncio.run(_run_query())


@app.command()
def replay(
    run_id: Annotated[str, typer.Argument(help="UUID of the run to replay from DB")]
) -> None:
    """Fetch run steps/trace from DB and re-render."""
    console.print(f"[bold blue]Replaying trace for Run ID:[/bold blue] {run_id}")

    async def _replay():
        try:
            r_uuid = uuid.UUID(run_id)
        except ValueError:
            console.print("[bold red]Invalid UUID format.[/bold red]")
            return

        settings = get_settings()
        await init_engine(settings.DATABASE_URL)
        try:
            async for session in get_session():
                from app.db.repositories.step import StepRepo
                repo = StepRepo(session)
                steps = await repo.list_for_run(r_uuid)

                if not steps:
                    console.print(f"[yellow]No steps found in DB for run_id={run_id}[/yellow]")
                    return

                table = Table(title=f"Replay Trace: {run_id}", show_header=True)
                table.add_column("Seq", justify="right", width=6)
                table.add_column("Event Type", width=18)
                table.add_column("Message", width=50)

                for step in steps:
                    table.add_row(str(step.seq), step.event_type, step.message or "")

                console.print(table)
        finally:
            await close_engine()

    asyncio.run(_replay())


if __name__ == "__main__":
    app()
