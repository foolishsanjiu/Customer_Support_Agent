from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, TimestampMixin
from app.models.enums import TicketCategory, TicketStatus


class Ticket(TimestampMixin, Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    status: Mapped[TicketStatus] = mapped_column(
        SAEnum(TicketStatus, native_enum=False, length=24, create_constraint=True),
        nullable=False,
        default=TicketStatus.OPEN,
    )
    category: Mapped[TicketCategory] = mapped_column(
        SAEnum(TicketCategory, native_enum=False, length=24, create_constraint=True),
        nullable=False,
    )
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    conversation_summary: Mapped[str | None] = mapped_column(Text(), nullable=True)
    summary_through_message_id: Mapped[int | None] = mapped_column(Integer(), nullable=True)
