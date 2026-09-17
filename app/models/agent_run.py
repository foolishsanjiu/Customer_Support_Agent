from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, TimestampMixin
from app.models.enums import AgentRunStatus


class AgentRun(TimestampMixin, Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    trigger_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("ticket_messages.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
        unique=True,
    )
    continuation_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT"), nullable=True
    )
    status: Mapped[AgentRunStatus] = mapped_column(
        SAEnum(AgentRunStatus, native_enum=False, length=32, create_constraint=True),
        nullable=False,
        default=AgentRunStatus.PENDING,
    )
    intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    current_node: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    success: Mapped[bool | None] = mapped_column(Boolean(), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text(), nullable=True)
    recovery_attempts: Mapped[int] = mapped_column(
        Integer(), nullable=False, default=0, server_default="0"
    )
    awaiting_customer_input: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=False, server_default="0"
    )
    missing_fields: Mapped[list[str] | None] = mapped_column(JSON(), nullable=True)
