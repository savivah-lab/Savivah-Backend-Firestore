"""
Same caveat as before: this payload shape is an ASSUMPTION, not confirmed
against real Fargo docs — see schemas/order.py.
"""
from fastapi import APIRouter, Depends, HTTPException, Header
from app.core.firestore_client import get_db
from app.core.config import settings
from app.services.firestore_helpers import now
from app.services.escrow import mark_delivered_awaiting_release, refund_order
from app.schemas.order import FargoWebhookPayload

router = APIRouter(prefix="/api/orders/webhooks", tags=["webhooks"])


@router.post("/fargo")
async def fargo_webhook(body: FargoWebhookPayload, db=Depends(get_db), x_fargo_signature: str | None = Header(default=None)):
    # TODO: verify x_fargo_signature once Fargo's real signing scheme is
    # known — currently NOT checked beyond presence, same gap as before.
    if settings.FARGO_WEBHOOK_SECRET and not x_fargo_signature:
        raise HTTPException(status_code=401, detail="Missing webhook signature")

    docs = [d async for d in db.collection("deliveries").where("fargo_tracking_id", "==", body.fargo_tracking_id).limit(1).stream()]
    if not docs:
        raise HTTPException(status_code=404, detail="Unknown tracking id")
    delivery_ref = docs[0].reference
    delivery = docs[0].to_dict()
    order_id = delivery["order_id"]  # == docs[0].id, but read explicitly for clarity

    updates = {"status": body.status, "last_status_at": now()}
    if body.status == "failed":
        updates["attempts"] = delivery.get("attempts", 0) + 1
    await delivery_ref.update(updates)

    if body.status == "delivered":
        await mark_delivered_awaiting_release(db, order_id)
    elif body.status == "failed" and updates.get("attempts", 0) >= 2:
        await refund_order(db, order_id)

    return {"ok": True}
