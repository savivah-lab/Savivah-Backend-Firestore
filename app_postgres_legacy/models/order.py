import uuid
from datetime import datetime
from sqlalchemy import String, Text, Numeric, Integer, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    store_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("stores.id"))
    subtotal: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    commission_rate: Mapped[float] = mapped_column(Numeric(5, 4), default=0.10)
    commission_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    payout_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="KES")
    # pending_payment|escrow_held|shipped|delivered|delivery_failed|refunded|disputed
    status: Mapped[str] = mapped_column(String(20), default="pending_payment")
    delivery_address: Mapped[str] = mapped_column(Text, nullable=False)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payout_released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    auto_release_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"))
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"))
    product_name: Mapped[str] = mapped_column(Text, nullable=False)  # snapshot at time of purchase
    unit_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
