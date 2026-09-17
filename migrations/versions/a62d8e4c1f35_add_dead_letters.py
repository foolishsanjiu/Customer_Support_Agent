"""add dead letters

Revision ID: a62d8e4c1f35
Revises: f34a1c9d7b20
Create Date: 2026-09-17 16:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a62d8e4c1f35"
down_revision: str | None = "f34a1c9d7b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dead_letters",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("approval_id", sa.Integer(), nullable=True),
        sa.Column("task_name", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "OPEN",
                "REPLAYING",
                "REPLAYED",
                name="deadletterstatus",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("reason_code", sa.String(length=100), nullable=False),
        sa.Column("error_type", sa.String(length=100), nullable=True),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("replay_count", sa.Integer(), nullable=False),
        sa.Column("replayed_by", sa.String(length=120), nullable=True),
        sa.Column("last_replayed_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["approvals.id"],
            name="fk_dead_letters_approval_id_approvals",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_dead_letters_run_id_agent_runs",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_dead_letters"),
        sa.UniqueConstraint("run_id", "task_name", name="uq_dead_letters_run_task"),
    )
    op.create_index(
        op.f("ix_dead_letters_approval_id"),
        "dead_letters",
        ["approval_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_dead_letters_run_id"),
        "dead_letters",
        ["run_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_dead_letters_run_id"), table_name="dead_letters")
    op.drop_index(op.f("ix_dead_letters_approval_id"), table_name="dead_letters")
    op.drop_table("dead_letters")
