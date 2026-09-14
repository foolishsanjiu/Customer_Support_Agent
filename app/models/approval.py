from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base
from app.models.enums import ApprovalStatus, PrincipalRole, ToolRiskLevel


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    tool_call_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_level: Mapped[ToolRiskLevel] = mapped_column(
        SAEnum(
            ToolRiskLevel,
            name="approvalrisklevel",
            native_enum=False,
            length=8,
            create_constraint=True,
        ),
        nullable=False,
    )
    arguments_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON(), nullable=False)
    action_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text(), nullable=False)
    status: Mapped[ApprovalStatus] = mapped_column(
        SAEnum(
            ApprovalStatus,
            name="approvalstatus",
            native_enum=False,
            length=16,
            create_constraint=True,
        ),
        nullable=False,
        default=ApprovalStatus.PENDING,
    )
    required_role: Mapped[PrincipalRole] = mapped_column(
        SAEnum(
            PrincipalRole,
            name="approvalprincipalrole",
            native_enum=False,
            length=24,
            create_constraint=True,
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    rejected_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    ticket_id: Mapped[int | None] = mapped_column(
        ForeignKey("tickets.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    approval_id: Mapped[int | None] = mapped_column(
        ForeignKey("approvals.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    tool_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_role: Mapped[PrincipalRole | None] = mapped_column(
        SAEnum(
            PrincipalRole,
            name="auditprincipalrole",
            native_enum=False,
            length=24,
            create_constraint=True,
        ),
        nullable=True,
    )
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON(), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, server_default=func.now()
    )
