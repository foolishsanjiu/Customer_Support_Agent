from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, TimestampMixin
from app.models.enums import DeadLetterStatus


class DeadLetter(TimestampMixin, Base):
    __tablename__ = "dead_letters"
    __table_args__ = (UniqueConstraint("run_id", "task_name", name="uq_dead_letters_run_task"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    approval_id: Mapped[int | None] = mapped_column(
        ForeignKey("approvals.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    task_name: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[DeadLetterStatus] = mapped_column(
        SAEnum(DeadLetterStatus, native_enum=False, length=16, create_constraint=True),
        nullable=False,
        default=DeadLetterStatus.OPEN,
    )
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    failure_count: Mapped[int] = mapped_column(Integer(), nullable=False, default=1)
    replay_count: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    replayed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_replayed_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
