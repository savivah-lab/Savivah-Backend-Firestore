"""
Fargo notifies us of delivery status changes here. The exact payload shape
is an ASSUMPTION (see schemas/order.py FargoWebhookPayload docstring) —
confirm against Fargo's real API docs before production use.
"""
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_db
from app.core.config import settings
from app.models.delivery import Delivery
from app.schemas.order import FargoWebhookPayload
from app.services.escrow import mark_delivered_awaiting_release, refund_order

router = APIRouter(prefix="/api/orders/webhooks", tags=["webhooks"])


@router.post("/fargo")
async def fargo_webhook(
    body: FargoWebhookPayload,
    db: AsyncSession = Depends(get_db),
    x_fargo_signature: str | None = Header(default=None),
):
    # TODO: verify x_fargo_signature against settings.FARGO_WEBHOOK_SECRET
    # once Fargo's real signing scheme is known — currently NOT checked,
    # meaning this endpoint trusts any request with the right JSON shape.
    if settings.FARGO_WEBHOOK_SECRET and not x_fargo_signature:
        raise HTTPException(status_code=401, detail="Missing webhook signature")

    delivery = (await db.execute(
        select(Delivery).where(Delivery.fargo_tracking_id == body.fargo_tracking_id)
    )).scalar_one_or_none()
    if not delivery:
        raise HTTPException(status_code=404, detail="Unknown tracking id")

    delivery.status = body.status
    if body.status == "failed":
        delivery.attempts += 1
    await db.commit()

    if body.status == "delivered":
        await mark_delivered_awaiting_release(db, delivery.order_id)
    elif body.status == "failed" and delivery.attempts >= 2:
        await refund_order(db, delivery.order_id)

    return {"ok": True}
