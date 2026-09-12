import uuid
from datetime import datetime
from sqlalchemy import String, Text, Numeric, SmallInteger, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id"))
    pesapal_order_tracking_id: Mapped[str | None] = mapped_column(Text, unique=True, nullable=True)
    pesapal_merchant_reference: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    payment_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_code: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)  # 0 invalid,1 completed,2 failed,3 reversed
    status_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmation_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_ipn_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
