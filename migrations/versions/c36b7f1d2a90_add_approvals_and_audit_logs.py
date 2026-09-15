"""add approvals and audit logs

Revision ID: c36b7f1d2a90
Revises: a845be21f670
Create Date: 2026-09-15 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c36b7f1d2a90"
down_revision: str | None = "a845be21f670"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approvals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("tool_call_id", sa.String(length=64), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("requested_by", sa.String(length=64), nullable=False),
        sa.Column(
            "risk_level",
            sa.Enum("L1", "L2", "L3", name="approvalrisklevel", native_enum=False),
            nullable=False,
        ),
        sa.Column("arguments_snapshot", sa.JSON(), nullable=False),
        sa.Column("action_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "APPROVED",
                "REJECTED",
                "EXPIRED",
                "CANCELLED",
                name="approvalstatus",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "required_role",
            sa.Enum(
                "CUSTOMER",
                "SUPPORT_AGENT",
                "MANAGER",
                "ADMIN",
                name="approvalprincipalrole",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("approved_by", sa.String(length=64), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("rejected_by", sa.String(length=64), nullable=True),
        sa.Column("rejected_at", sa.DateTime(), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approvals")),
        sa.UniqueConstraint("tool_call_id", name=op.f("uq_approvals_tool_call_id")),
    )
    op.create_index(op.f("ix_approvals_run_id"), "approvals", ["run_id"])
    op.create_index(op.f("ix_approvals_ticket_id"), "approvals", ["ticket_id"])
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("ticket_id", sa.Integer(), nullable=True),
        sa.Column("approval_id", sa.Integer(), nullable=True),
        sa.Column("tool_call_id", sa.String(length=64), nullable=True),
        sa.Column("actor_id", sa.String(length=64), nullable=True),
        sa.Column(
            "actor_role",
            sa.Enum(
                "CUSTOMER",
                "SUPPORT_AGENT",
                "MANAGER",
                "ADMIN",
                name="auditprincipalrole",
                native_enum=False,
            ),
            nullable=True,
        ),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approval_id"], ["approvals.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    for column in ("event_type", "run_id", "ticket_id", "approval_id", "tool_call_id", "trace_id"):
        op.create_index(op.f(f"ix_audit_logs_{column}"), "audit_logs", [column])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("approvals")
