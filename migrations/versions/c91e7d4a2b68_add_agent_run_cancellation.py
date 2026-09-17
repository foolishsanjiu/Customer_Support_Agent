"""add agent run cancellation states

Revision ID: c91e7d4a2b68
Revises: a62d8e4c1f35
Create Date: 2026-09-17 13:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c91e7d4a2b68"
down_revision: str | None = "a62d8e4c1f35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "agentrunstatus"
P0_STATUSES = (
    "PENDING",
    "RUNNING",
    "WAITING_APPROVAL",
    "RESUME_PENDING",
    "RECOVERY_REQUIRED",
    "SUCCEEDED",
    "FAILED",
)
P1_STATUSES = (*P0_STATUSES, "CANCEL_REQUESTED", "CANCELLED")


def _status_check(statuses: tuple[str, ...]) -> str:
    values = ", ".join(f"'{status}'" for status in statuses)
    return f"status IN ({values})"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, "agent_runs", type_="check")
    op.create_check_constraint(CONSTRAINT, "agent_runs", _status_check(P1_STATUSES))


def downgrade() -> None:
    op.execute(
        "UPDATE agent_runs SET status = 'FAILED', success = 0, "
        "error_code = 'cancellation_state_downgraded' "
        "WHERE status IN ('CANCEL_REQUESTED', 'CANCELLED')"
    )
    op.drop_constraint(CONSTRAINT, "agent_runs", type_="check")
    op.create_check_constraint(CONSTRAINT, "agent_runs", _status_check(P0_STATUSES))
