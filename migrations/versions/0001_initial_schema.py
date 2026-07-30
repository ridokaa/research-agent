"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-07-29 00:00:00.000000

Creates all tables and required indexes for the agentic-loop project:
  - conversations
  - messages
  - runs
  - run_steps
  - tool_invocations
  - execution_memory

Indexes:
  - ix_run_steps_run_id_seq          ON run_steps(run_id, seq)
  - ix_tool_invocations_run_id       ON tool_invocations(run_id)
  - ix_messages_conversation_id_seq  ON messages(conversation_id, seq)
  - ix_runs_created_at_desc          ON runs(started_at DESC)
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ----------------------------------------------------------------
    # conversations
    # ----------------------------------------------------------------
    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # ----------------------------------------------------------------
    # messages
    # ----------------------------------------------------------------
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool_call_id", sa.String(256), nullable=True),
        sa.Column("tool_name", sa.String(128), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("token_in", sa.Integer(), nullable=True),
        sa.Column("token_out", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_messages_conversation_id_seq",
        "messages",
        ["conversation_id", "seq"],
    )

    # ----------------------------------------------------------------
    # runs
    # ----------------------------------------------------------------
    op.create_table(
        "runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_query", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("final_answer", sa.Text(), nullable=True),
        sa.Column("iterations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("credits_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.BigInteger(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    # DESC index for listing runs by most recent first
    op.create_index(
        "ix_runs_created_at_desc",
        "runs",
        [sa.text("started_at DESC")],
    )

    # ----------------------------------------------------------------
    # run_steps
    # ----------------------------------------------------------------
    op.create_table(
        "run_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("run_id", "seq", name="uq_run_steps_run_id_seq"),
    )
    op.create_index(
        "ix_run_steps_run_id_seq",
        "run_steps",
        ["run_id", "seq"],
    )

    # ----------------------------------------------------------------
    # tool_invocations
    # ----------------------------------------------------------------
    op.create_table(
        "tool_invocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_seq", sa.Integer(), nullable=True),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("arguments", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("credits_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_tool_invocations_run_id",
        "tool_invocations",
        ["run_id"],
    )

    # ----------------------------------------------------------------
    # execution_memory
    # ----------------------------------------------------------------
    op.create_table(
        "execution_memory",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("iteration", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(256), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("origin_tool", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_execution_memory_run_id_iteration",
        "execution_memory",
        ["run_id", "iteration"],
    )


def downgrade() -> None:
    op.drop_table("execution_memory")
    op.drop_table("tool_invocations")
    op.drop_table("run_steps")
    op.drop_table("runs")
    op.drop_table("messages")
    op.drop_table("conversations")
