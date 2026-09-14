"""add tool runtime audit

Revision ID: a845be21f670
Revises: 58c85edab11b
Create Date: 2026-09-14 14:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a845be21f670"
down_revision: str | None = "58c85edab11b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "idempotency_records",
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("tool_call_id", sa.String(length=64), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "IN_PROGRESS",
                "SUCCEEDED",
                "FAILED",
                name="idempotencystatus",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_idempotency_records")),
    )
    op.create_table(
        "tool_calls",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tool_call_id", sa.String(length=64), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column(
            "risk_level",
            sa.Enum(
                "L1",
                "L2",
                "L3",
                name="toolrisklevel",
                native_enum=False,
                create_constraint=True,
                length=8,
            ),
            nullable=False,
        ),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "RUNNING",
                "EXECUTED",
                "SUCCEEDED",
                "FAILED",
                name="toolcallstatus",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
            name=op.f("fk_tool_calls_agent_run_id_agent_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["tickets.id"],
            name=op.f("fk_tool_calls_ticket_id_tickets"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tool_calls")),
        sa.UniqueConstraint("tool_call_id", name=op.f("uq_tool_calls_tool_call_id")),
    )
    op.create_index(op.f("ix_tool_calls_agent_run_id"), "tool_calls", ["agent_run_id"])
    op.create_index(op.f("ix_tool_calls_ticket_id"), "tool_calls", ["ticket_id"])
    op.create_index(op.f("ix_tool_calls_trace_id"), "tool_calls", ["trace_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_tool_calls_trace_id"), table_name="tool_calls")
    op.drop_index(op.f("ix_tool_calls_ticket_id"), table_name="tool_calls")
    op.drop_index(op.f("ix_tool_calls_agent_run_id"), table_name="tool_calls")
    op.drop_table("tool_calls")
    op.drop_table("idempotency_records")
