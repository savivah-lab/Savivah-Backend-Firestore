import uuid
from datetime import datetime
from sqlalchemy import String, Text, SmallInteger, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class Delivery(Base):
    __tablename__ = "deliveries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id"), unique=True)
    fargo_tracking_id: Mapped[str | None] = mapped_column(Text, unique=True, nullable=True)
    proof_of_shipment_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # awaiting_pickup|in_transit|delivered|failed|returned
    status: Mapped[str] = mapped_column(String(20), default="awaiting_pickup")
    attempts: Mapped[int] = mapped_column(SmallInteger, default=0)
    last_status_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_webhook_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
