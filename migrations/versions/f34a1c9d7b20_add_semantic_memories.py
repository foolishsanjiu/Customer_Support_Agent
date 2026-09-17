"""add semantic memories

Revision ID: f34a1c9d7b20
Revises: d81e7c4a2f19
Create Date: 2026-09-17 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f34a1c9d7b20"
down_revision: str | None = "d81e7c4a2f19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "semantic_memories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("source_ticket_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.String(length=500), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name="fk_semantic_memories_customer_id_customers",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_ticket_id"],
            ["tickets.id"],
            name="fk_semantic_memories_source_ticket_id_tickets",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_semantic_memories"),
        sa.UniqueConstraint(
            "customer_id", "content_hash", name="uq_semantic_memories_customer_hash"
        ),
    )
    op.create_index(
        op.f("ix_semantic_memories_customer_id"),
        "semantic_memories",
        ["customer_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_semantic_memories_source_ticket_id"),
        "semantic_memories",
        ["source_ticket_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_semantic_memories_source_ticket_id"), table_name="semantic_memories")
    op.drop_index(op.f("ix_semantic_memories_customer_id"), table_name="semantic_memories")
    op.drop_table("semantic_memories")
