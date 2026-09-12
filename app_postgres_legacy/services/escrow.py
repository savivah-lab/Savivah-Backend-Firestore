"""
The core money-holding rules: hold funds on payment confirmation, release
to the seller after delivery + a grace window (unless a dispute is open),
refund when shipping/delivery fails. Same rules as the previous
implementation — this is a port, not a redesign.
"""
import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.order import Order
from app.models.store import Store
from app.models.dispute import Dispute
from app.models.payout import Payout

COMMISSION_RATE = 0.10
AUTO_RELEASE_DAYS = 5


async def confirm_payment_escrow(db: AsyncSession, order_id: uuid.UUID) -> None:
    order = await db.get(Order, order_id)
    if order and order.status == "pending_payment":
        order.status = "escrow_held"
        await db.commit()


async def mark_shipped(db: AsyncSession, order_id: uuid.UUID) -> None:
    order = await db.get(Order, order_id)
    if order and order.status == "escrow_held":
        order.status = "shipped"
        order.shipped_at = datetime.now(timezone.utc)
        await db.commit()


async def mark_delivered_awaiting_release(db: AsyncSession, order_id: uuid.UUID) -> None:
    order = await db.get(Order, order_id)
    if order and order.status == "shipped":
        order.status = "delivered"
        order.delivered_at = datetime.now(timezone.utc)
        order.auto_release_at = datetime.now(timezone.utc) + timedelta(days=AUTO_RELEASE_DAYS)
        await db.commit()


async def release_payout(db: AsyncSession, order_id: uuid.UUID) -> Payout:
    order = await db.get(Order, order_id)
    if not order:
        raise ValueError("Order not found")

    open_dispute = (await db.execute(
        select(Dispute).where(Dispute.order_id == order_id, Dispute.status == "open")
    )).scalar_one_or_none()
    if open_dispute:
        raise ValueError("Cannot release payout while a dispute is open")

    store = await db.get(Store, order.store_id)
    payout = Payout(
        store_id=order.store_id,
        order_id=order.id,
        amount=order.payout_amount,
        method=store.payout_method if store else None,
        status="pending",
    )
    db.add(payout)
    order.payout_released_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(payout)
    return payout
    # In production: trigger an actual M-Pesa B2C or bank transfer here (see
    # the connection reference doc — this is NOT automated anywhere yet),
    # then flip payout.status to 'sent' once the transfer confirms.


async def refund_order(db: AsyncSession, order_id: uuid.UUID) -> None:
    order = await db.get(Order, order_id)
    if order:
        order.status = "refunded"
        await db.commit()
    # In production: trigger a Pesapal RefundRequest call here using the
    # stored pesapal_order_tracking_id — not implemented yet.


async def run_auto_release_sweep(db: AsyncSession) -> int:
    """Intended to run on a schedule (see workers/payout_sweep.py)."""
    now = datetime.now(timezone.utc)
    disputed_order_ids = select(Dispute.order_id).where(Dispute.status == "open")
    rows = (await db.execute(
        select(Order.id).where(
            Order.status == "delivered",
            Order.auto_release_at <= now,
            Order.id.notin_(disputed_order_ids),
        )
    )).scalars().all()

    for order_id in rows:
        await release_payout(db, order_id)
    return len(rows)
