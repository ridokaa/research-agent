"""AgentState, ExecutionMemory, ConversationHistory, and snapshot persistence.

PROMPT 7 implementation.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ExecutionMemory as ExecutionMemoryORM
from app.db.models import Message as MessageORM
from app.db.models import Run as RunORM
from app.tools.base import ToolResult


def estimate_tokens(text: str) -> int:
    """Estimate token count for a text string using length heuristic (len // 4)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


class Message(BaseModel):
    """Conversation message representation for state tracking and audit persistence."""

    id: UUID = Field(default_factory=uuid.uuid4)
    conversation_id: UUID | None = None
    role: Literal["user", "assistant", "tool", "system"]
    content: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    seq: int = 0
    token_in: int | None = None
    token_out: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class MemoryItem(BaseModel):
    """Structured memory item recorded per tool invocation / iteration."""

    iteration: int
    key: str
    origin_tool: str
    summary: str
    value: Any
    tokens_estimate: int = 0
    status: Literal["success", "error", "timeout", "empty"] = "success"
    arguments: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class ExecutionMemory(BaseModel):
    """Structured execution memory managing tool results across iterations.

    Provides deduplication caching and context rendering for prompt assembly.
    """

    items: list[MemoryItem] = Field(default_factory=list)

    def __iter__(self):
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int) -> MemoryItem:
        return self.items[index]

    def append(self, item: MemoryItem) -> None:
        self.items.append(item)

    @staticmethod
    def _normalize_args(args: dict[str, Any] | None) -> str:
        if not args:
            return "{}"
        try:
            return json.dumps(args, sort_keys=True)
        except Exception:
            return str(args)

    def is_cached(self, tool_name: str, arguments: dict[str, Any] | None = None) -> bool:
        """Check if an identical tool call (name + arguments) was previously recorded."""
        return self.get_cached(tool_name, arguments) is not None

    def get_cached(
        self, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> MemoryItem | None:
        """Return cached MemoryItem for identical tool call (name + arguments) if found."""
        target_args_str = self._normalize_args(arguments)
        for item in self.items:
            if item.origin_tool == tool_name:
                if self._normalize_args(item.arguments) == target_args_str:
                    return item
        return None

    def add(
        self,
        tool_result: ToolResult | MemoryItem,
        iteration: int = 0,
        arguments: dict[str, Any] | None = None,
        key: str | None = None,
        summary: str | None = None,
    ) -> MemoryItem:
        """Add a tool result or memory item to execution memory."""
        if isinstance(tool_result, MemoryItem):
            self.items.append(tool_result)
            return tool_result

        args = arguments or {}
        if summary is None:
            if tool_result.status == "error":
                summary = f"Error: {tool_result.error or 'Unknown tool error'}"
            elif tool_result.status == "empty":
                summary = "Empty result returned"
            else:
                raw_str = str(tool_result.data) if tool_result.data is not None else "Success"
                summary = raw_str[:200]

        if key is None:
            key = f"{tool_result.tool_name}:{iteration}:{len(self.items) + 1}"

        val = (
            tool_result.data
            if tool_result.status == "success"
            else (tool_result.error or tool_result.data)
        )
        text_repr = f"{summary} {val}"
        tok_est = estimate_tokens(text_repr)

        item = MemoryItem(
            iteration=iteration,
            key=key,
            origin_tool=tool_result.tool_name,
            summary=summary,
            value=val,
            tokens_estimate=tok_est,
            status=tool_result.status,
            arguments=args,
        )
        self.items.append(item)
        return item

    def get_context_block(self, max_tokens: int = 2000, current_iteration: int = 0) -> str:
        """Render memory into a concise text block for prompt injection.

        Priority & Filtering Rules:
        - Priority: Newer items first + Success items first.
        - Pruning: Discard error/empty items older than 2 iterations
          (i.e. current_iteration - item.iteration >= 2).
        - Respect max_tokens limit.
        """
        valid_items: list[MemoryItem] = []
        for item in self.items:
            if item.status in ("error", "empty") and (current_iteration - item.iteration >= 2):
                continue
            valid_items.append(item)

        if not valid_items:
            return ""

        # Priority sort: newest iteration first, success status first
        sorted_items = sorted(
            valid_items,
            key=lambda x: (x.iteration, 1 if x.status == "success" else 0),
            reverse=True,
        )

        selected: list[MemoryItem] = []
        budget = max_tokens
        header = "### Execution Memory"
        budget -= estimate_tokens(header)

        for item in sorted_items:
            line_text = f"- [Iter {item.iteration} | {item.origin_tool}] {item.key}: {item.summary}"
            cost = item.tokens_estimate or estimate_tokens(line_text)
            if cost <= budget:
                selected.append(item)
                budget -= cost

        if not selected:
            return ""

        # Re-sort selected items into chronological order
        selected.sort(key=lambda x: (x.iteration, x.created_at))

        lines = [header]
        for item in selected:
            lines.append(
                f"- [Iter {item.iteration} | {item.origin_tool}] {item.key}: {item.summary}"
            )

        return "\n".join(lines)


class AgentState(BaseModel):
    """Execution state and credit tracking for the agent loop."""

    run_id: UUID = Field(default_factory=uuid.uuid4)
    conversation_id: UUID = Field(default_factory=uuid.uuid4)
    user_query: str = ""
    iteration: int = 0
    max_iterations: int = 8
    messages: list[Message] = Field(default_factory=list)
    execution_memory: ExecutionMemory = Field(default_factory=ExecutionMemory)
    last_response_id: str | None = None
    credits_used: int = 0
    credits_budget: int = 100
    started_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    status: Literal["running", "success", "failed", "exhausted", "timeout"] = "running"

    @field_validator("execution_memory", mode="before")
    @classmethod
    def _coerce_execution_memory(cls, v: Any) -> ExecutionMemory:
        if isinstance(v, list):
            return ExecutionMemory(items=v)
        if isinstance(v, ExecutionMemory):
            return v
        if isinstance(v, dict):
            return ExecutionMemory(**v)
        return ExecutionMemory()

    def add_credits(self, amount: int) -> None:
        """Accumulate consumed credits."""
        self.credits_used += amount


class ConversationHistory:
    """Manages loading and per-iteration DB persistence of conversation messages."""

    def __init__(self, session: AsyncSession, conversation_id: UUID) -> None:
        self.session = session
        self.conversation_id = conversation_id
        self._persisted_ids: set[UUID] = set()

    async def load(self) -> list[Message]:
        """Load conversation history from DB table `messages` ordered by seq ASC."""
        stmt = (
            select(MessageORM)
            .where(MessageORM.conversation_id == self.conversation_id)
            .order_by(MessageORM.seq.asc())
        )
        result = await self.session.execute(stmt)
        orm_msgs = result.scalars().all()

        messages: list[Message] = []
        for m in orm_msgs:
            msg = Message(
                id=m.id,
                conversation_id=m.conversation_id,
                role=m.role,  # type: ignore[arg-type]
                content=m.content,
                tool_call_id=m.tool_call_id,
                tool_name=m.tool_name,
                seq=m.seq,
                token_in=m.token_in,
                token_out=m.token_out,
                created_at=m.created_at,
            )
            messages.append(msg)
            self._persisted_ids.add(m.id)
        return messages

    async def persist_iteration(
        self,
        messages: list[Message],
        run_id: UUID | None = None,
        last_response_id: str | None = None,
    ) -> None:
        """Persist new messages to DB at the end of each iteration."""
        max_seq = 0
        stmt = (
            select(MessageORM.seq)
            .where(MessageORM.conversation_id == self.conversation_id)
            .order_by(MessageORM.seq.desc())
            .limit(1)
        )
        res = await self.session.execute(stmt)
        last_seq = res.scalar_one_or_none()
        if last_seq is not None:
            max_seq = last_seq

        for msg in messages:
            if msg.id not in self._persisted_ids:
                max_seq += 1
                msg.seq = max_seq
                orm_msg = MessageORM(
                    id=msg.id,
                    conversation_id=self.conversation_id,
                    role=msg.role,
                    content=msg.content,
                    tool_call_id=msg.tool_call_id,
                    tool_name=msg.tool_name,
                    seq=msg.seq,
                    token_in=msg.token_in,
                    token_out=msg.token_out,
                    created_at=msg.created_at,
                )
                self.session.add(orm_msg)
                self._persisted_ids.add(msg.id)

        if run_id and last_response_id is not None:
            stmt_run = select(RunORM).where(RunORM.id == run_id)
            run_res = await self.session.execute(stmt_run)
            run_obj = run_res.scalar_one_or_none()
            if run_obj:
                run_obj.last_response_id = last_response_id

        await self.session.flush()


def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return _to_jsonable(obj.model_dump(mode="json"))
    if hasattr(obj, "model_dump"):
        try:
            return _to_jsonable(obj.model_dump(mode="json"))
        except Exception:
            pass
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    return str(obj)


async def persist_execution_memory_snapshot(
    session: AsyncSession,
    run_id: UUID,
    iteration: int,
    execution_memory: ExecutionMemory,
) -> None:
    """Write snapshot lines to execution_memory table for given iteration."""
    for item in execution_memory.items:
        if item.iteration == iteration:
            jsonable_val = _to_jsonable(item.value)
            val = jsonable_val if isinstance(jsonable_val, dict) else {"value": jsonable_val}
            mem_orm = ExecutionMemoryORM(
                id=uuid.uuid4(),
                run_id=run_id,
                iteration=iteration,
                key=item.key,
                value=val,
                origin_tool=item.origin_tool,
                created_at=item.created_at,
            )
            session.add(mem_orm)
    await session.flush()
