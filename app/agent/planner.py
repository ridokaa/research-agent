"""Planner — prompt construction, reflection injection, and self-check finalization.

PROMPT 8 implementation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.agent.state import AgentState
from app.llm.schemas import compact_input

PROMPTS_DIR = Path(__file__).parent / "prompts"


class Planner:
    """Planner helper that constructs system prompts, request payloads, and reflection messages."""

    def __init__(self, prompts_dir: Path | None = None) -> None:
        self.prompts_dir = prompts_dir or PROMPTS_DIR
        self._system_template = self._load_template("system.md")
        self._reflection_template = self._load_template("reflection.md")
        self._finalize_template = self._load_template("finalize.md")

    def _load_template(self, filename: str) -> str:
        file_path = self.prompts_dir / filename
        if file_path.exists():
            return file_path.read_text(encoding="utf-8")
        return ""

    def build_system_prompt(self, state: AgentState) -> str:
        """Render the system prompt with current state parameters."""
        mem_block = state.execution_memory.get_context_block(
            max_tokens=2000, current_iteration=state.iteration
        )
        if not mem_block:
            mem_block = "(Belum ada data dari tool)"

        from datetime import datetime, UTC
        import zoneinfo
        from app.config import get_settings

        settings = get_settings()
        try:
            tz = zoneinfo.ZoneInfo(settings.AGENT_TIMEZONE)
        except Exception:
            tz = UTC

        current_date_str = datetime.now(tz=tz).strftime("%A, %d %B %Y")

        return self._system_template.format(
            iteration=state.iteration,
            max_iterations=state.max_iterations,
            credits_used=state.credits_used,
            credits_budget=state.credits_budget,
            execution_memory=mem_block,
            current_date=current_date_str,
        )

    def build_request(
        self,
        state: AgentState,
        pending_inputs: list[dict[str, Any]] | None = None,
        store: bool = True,
    ) -> tuple[str, str | list[dict[str, Any]]]:
        """Construct system instructions and input payload for LLMClient.respond.

        Parameters
        ----------
        state:
            Current AgentState.
        pending_inputs:
            List of function_call_output or user/message items accumulated in the previous step.
        store:
            If True, only send pending_inputs when last_response_id is present.
            If False, send compacted full history.

        Returns
        -------
        tuple[str, str | list[dict[str, Any]]]
            (instructions, input_payload)
        """
        instructions = self.build_system_prompt(state)

        # Iteration 1 or initial request
        if state.last_response_id is None or not pending_inputs:
            input_payload: str | list[dict[str, Any]] = state.user_query
            return instructions, input_payload

        # Continuation iterations
        if store:
            # Server-side state enabled: send only new items + previous_response_id
            return instructions, pending_inputs

        # Server-side state disabled: build full compacted conversation
        history_items: list[dict[str, Any]] = [
            {"type": "message", "role": "user", "content": state.user_query}
        ]
        history_items.extend(pending_inputs)
        compacted = compact_input(history_items, keep_last_n=10)
        return instructions, compacted

    def build_reflection_input(self, missing: str, reason: str = "Jawaban belum didukung data lengkap") -> dict[str, Any]:
        """Build reflection message item using reflection.md template."""
        content = self._reflection_template.format(
            reason=reason or "Jawaban belum didukung data",
            missing=missing or "Informasi pendukung belum cukup",
        )
        return {
            "type": "message",
            "role": "user",
            "content": content,
        }

    def build_finalize_input(
        self,
        draft_answer: str,
        execution_memory_text: str = "",
        executed_tools: list[str] | None = None,
        available_tools: list[str] | None = None,
    ) -> str:
        """Build prompt for self-check finalization evaluation."""
        executed_str = ", ".join(executed_tools) if executed_tools else "(Belum ada tool)"
        available_str = ", ".join(available_tools) if available_tools else "(Semua tool)"
        return (
            f"{self._finalize_template}\n\n"
            f"--- Tool yang SUDAH Dipanggil ---\n{executed_str}\n\n"
            f"--- Tool TERSEDIA yang BELUM Dipanggil ---\n{available_str}\n\n"
            f"--- Draf Jawaban ---\n{draft_answer}\n\n"
            f"--- Data Tool Terkait ---\n{execution_memory_text or '(Tidak ada data tool)'}"
        )

    def build_fallback_prompt(self, user_query: str, execution_memory_text: str = "") -> str:
        """Build prompt for forced finalization on exhausted/timeout status."""
        return (
            f"Pertanyaan pengguna: {user_query}\n\n"
            f"Berikut data tool yang SUDAH TERKUMPUL di memori eksekusi:\n"
            f"{execution_memory_text or '(Belum ada data tool yang berhasil diperoleh)'}\n\n"
            f"Instruksi WAJIB:\n"
            f"1. Rangkum dan sajikan SELURUH data nyata yang ada di atas untuk menjawab pertanyaan pengguna.\n"
            f"2. Sebutkan angka, statistik, dan fakta spesifik yang ada di memori.\n"
            f"3. DILARANG MENYATAKAN bahwa 'tidak ada data' atau 'belum ada data' bila memori di atas memuat data!\n"
            f"4. Sebutkan keterbatasan bila ada aspek yang belum terjawab, namun JANGAN MENGABAIKAN data yang sudah berhasil terkumpul."
        )

    def build_synthesis_input(self, user_query: str, execution_memory_text: str = "") -> str:
        """Build prompt for proactive right-sized synthesis when synthesis_mode=True."""
        return (
            f"Pertanyaan pengguna: {user_query}\n\n"
            f"Berikut data ringkas yang SUDAH TERKUMPUL dari tool:\n"
            f"{execution_memory_text or '(Belum ada data tool yang diperoleh)'}\n\n"
            f"Instruksi Sintesis:\n"
            f"1. Analisis dan jawab pertanyaan pengguna secara komprehensif menggunakan data di atas.\n"
            f"2. Sertakan angka, statistik, topik, dan sentimen secara akurat dari data yang tersedia.\n"
            f"3. DILARANG MENYATAKAN bahwa 'tidak ada data' atau 'belum ada data' bila memori di atas memuat data!\n"
            f"4. Jangan mengabaikan data yang sudah ada, dan sebutkan keterbatasan hanya jika data memang tidak mencakup aspek tersebut."
        )

