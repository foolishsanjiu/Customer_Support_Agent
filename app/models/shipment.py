from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, TimestampMixin
from app.models.enums import ShipmentStatus


class Shipment(TimestampMixin, Base):
    __tablename__ = "shipments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    tracking_number: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    carrier: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[ShipmentStatus] = mapped_column(
        SAEnum(ShipmentStatus, native_enum=False, length=24, create_constraint=True),
        nullable=False,
    )
    current_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_event: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_updated_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    estimated_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
