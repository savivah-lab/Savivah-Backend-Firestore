import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_db
from app.deps import get_current_user, require_role
from app.models.user import User
from app.models.store import Store
from app.models.order import Order
from app.models.delivery import Delivery
from app.models.dispute import Dispute
from app.schemas.order import ShipRequest, DisputeRequest
from app.services.escrow import mark_shipped, release_payout

router = APIRouter(prefix="/api", tags=["orders"])


@router.get("/stores/{store_id}/orders")
async def store_orders(store_id: uuid.UUID, user: User = Depends(require_role("seller")), db: AsyncSession = Depends(get_db)):
    store = await db.get(Store, store_id)
    if not store or store.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Not your store")
    rows = (await db.execute(select(Order).where(Order.store_id == store_id).order_by(Order.created_at.desc()))).scalars().all()
    return rows


@router.post("/orders/{order_id}/ship")
async def ship_order(
    order_id: uuid.UUID, body: ShipRequest,
    user: User = Depends(require_role("seller")), db: AsyncSession = Depends(get_db),
):
    """No proof of shipment, no ship — enforced by ShipRequest requiring
    both fields, matching the business rule from the original scope doc."""
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    store = await db.get(Store, order.store_id)
    if not store or store.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Not your order")

    existing = (await db.execute(select(Delivery).where(Delivery.order_id == order_id))).scalar_one_or_none()
    if existing:
        existing.fargo_tracking_id = body.fargoTrackingId
        existing.proof_of_shipment_url = body.proofOfShipmentUrl
    else:
        db.add(Delivery(order_id=order_id, fargo_tracking_id=body.fargoTrackingId, proof_of_shipment_url=body.proofOfShipmentUrl))
    await db.commit()

    await mark_shipped(db, order_id)
    return {"ok": True}


@router.post("/orders/{order_id}/confirm-receipt")
async def confirm_receipt(order_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    order = await db.get(Order, order_id)
    if not order or order.customer_id != user.id:
        raise HTTPException(status_code=403, detail="Not your order")
    try:
        await release_payout(db, order_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.post("/orders/{order_id}/dispute")
async def raise_dispute(order_id: uuid.UUID, body: DisputeRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    order = await db.get(Order, order_id)
    if not order or order.customer_id != user.id:
        raise HTTPException(status_code=403, detail="Not your order")
    db.add(Dispute(order_id=order_id, raised_by=user.id, reason=body.reason, description=body.description))
    order.status = "disputed"
    await db.commit()
    return {"ok": True}
