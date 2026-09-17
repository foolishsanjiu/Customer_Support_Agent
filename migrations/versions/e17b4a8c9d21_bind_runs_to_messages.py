"""bind agent runs to customer messages

Revision ID: e17b4a8c9d21
Revises: c91e7d4a2b68
Create Date: 2026-09-17 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e17b4a8c9d21"
down_revision: str | None = "c91e7d4a2b68"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("trigger_message_id", sa.Integer(), nullable=True))
    op.add_column("agent_runs", sa.Column("continuation_run_id", sa.Integer(), nullable=True))
    op.add_column(
        "agent_runs",
        sa.Column("awaiting_customer_input", sa.Boolean(), server_default="0", nullable=False),
    )
    op.add_column("agent_runs", sa.Column("missing_fields", sa.JSON(), nullable=True))
    op.create_foreign_key(
        "fk_agent_runs_trigger_message_id_ticket_messages",
        "agent_runs",
        "ticket_messages",
        ["trigger_message_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_agent_runs_continuation_run_id_agent_runs",
        "agent_runs",
        "agent_runs",
        ["continuation_run_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ux_agent_runs_trigger_message_id",
        "agent_runs",
        ["trigger_message_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_agent_runs_trigger_message_id", table_name="agent_runs")
    op.drop_constraint(
        "fk_agent_runs_continuation_run_id_agent_runs", "agent_runs", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_agent_runs_trigger_message_id_ticket_messages", "agent_runs", type_="foreignkey"
    )
    op.drop_column("agent_runs", "missing_fields")
    op.drop_column("agent_runs", "awaiting_customer_input")
    op.drop_column("agent_runs", "continuation_run_id")
    op.drop_column("agent_runs", "trigger_message_id")
