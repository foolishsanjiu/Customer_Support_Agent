"""add conversation summary

Revision ID: d81e7c4a2f19
Revises: c36b7f1d2a90
Create Date: 2026-09-17 11:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d81e7c4a2f19"
down_revision: str | None = "c36b7f1d2a90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("conversation_summary", sa.Text(), nullable=True))
    op.add_column(
        "tickets",
        sa.Column("summary_through_message_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tickets", "summary_through_message_id")
    op.drop_column("tickets", "conversation_summary")
