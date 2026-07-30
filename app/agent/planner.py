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

        return self._system_template.format(
            iteration=state.iteration,
            max_iterations=state.max_iterations,
            credits_used=state.credits_used,
            credits_budget=state.credits_budget,
            execution_memory=mem_block,
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

    def build_finalize_input(self, draft_answer: str, execution_memory_text: str = "") -> str:
        """Build prompt for self-check finalization evaluation."""
        return (
            f"{self._finalize_template}\n\n"
            f"--- Draf Jawaban ---\n{draft_answer}\n\n"
            f"--- Data Tool Terkait ---\n{execution_memory_text or '(Tidak ada data tool)'}"
        )

    def build_fallback_prompt(self, user_query: str, execution_memory_text: str = "") -> str:
        """Build prompt for forced finalization on exhausted/timeout status."""
        return (
            f"Pertanyaan pengguna: {user_query}\n\n"
            f"Data yang sudah diperoleh dari tool sejauh ini:\n"
            f"{execution_memory_text or '(Belum ada data tool yang berhasil diperoleh)'}\n\n"
            f"Instruksi: Jawab sejujurnya dengan data yang ada, sebutkan keterbatasannya. "
            f"JANGAN mengarang data baru."
        )
