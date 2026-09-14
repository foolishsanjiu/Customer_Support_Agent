from sqlalchemy import Enum as SAEnum
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, TimestampMixin
from app.models.enums import CustomerLevel, CustomerStatus


class Customer(TimestampMixin, Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    level: Mapped[CustomerLevel] = mapped_column(
        SAEnum(CustomerLevel, native_enum=False, length=16, create_constraint=True),
        nullable=False,
        default=CustomerLevel.NORMAL,
    )
    status: Mapped[CustomerStatus] = mapped_column(
        SAEnum(CustomerStatus, native_enum=False, length=16, create_constraint=True),
        nullable=False,
        default=CustomerStatus.ACTIVE,
    )
