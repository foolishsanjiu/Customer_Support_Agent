from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base
from app.models.enums import IdempotencyStatus, ToolCallStatus, ToolRiskLevel


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tool_call_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    agent_run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_level: Mapped[ToolRiskLevel] = mapped_column(
        SAEnum(ToolRiskLevel, native_enum=False, length=8, create_constraint=True),
        nullable=False,
    )
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON(), nullable=False)
    status: Mapped[ToolCallStatus] = mapped_column(
        SAEnum(ToolCallStatus, native_enum=False, length=16, create_constraint=True),
        nullable=False,
    )
    result_summary: Mapped[str | None] = mapped_column(Text(), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    tool_call_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[IdempotencyStatus] = mapped_column(
        SAEnum(IdempotencyStatus, native_enum=False, length=16, create_constraint=True),
        nullable=False,
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, server_default=func.now(), onupdate=func.now()
    )
