from fastapi import APIRouter, Depends, HTTPException
from app.core.firestore_client import get_db
from app.deps import get_current_user, require_role
from app.services.firestore_helpers import new_id, now
from app.services.escrow import mark_shipped, release_payout
from app.schemas.order import ShipRequest, DisputeRequest

router = APIRouter(prefix="/api", tags=["orders"])


@router.get("/stores/{store_id}/orders")
async def store_orders(store_id: str, user: dict = Depends(require_role("seller")), db=Depends(get_db)):
    store_snap = await db.collection("stores").document(store_id).get()
    if not store_snap.exists or store_snap.to_dict().get("owner_id") != user["id"]:
        raise HTTPException(status_code=403, detail="Not your store")
    query = db.collection("orders").where("store_id", "==", store_id).order_by("created_at", direction="DESCENDING")
    out = []
    async for d in query.stream():
        o = d.to_dict()
        o["id"] = d.id
        out.append(o)
    return out


@router.post("/orders/{order_id}/ship")
async def ship_order(order_id: str, body: ShipRequest, user: dict = Depends(require_role("seller")), db=Depends(get_db)):
    """No proof of shipment, no ship — same rule as before."""
    order_snap = await db.collection("orders").document(order_id).get()
    if not order_snap.exists:
        raise HTTPException(status_code=404, detail="Order not found")
    order = order_snap.to_dict()
    store_snap = await db.collection("stores").document(order["store_id"]).get()
    if not store_snap.exists or store_snap.to_dict().get("owner_id") != user["id"]:
        raise HTTPException(status_code=403, detail="Not your order")

    # order_id doubles as the delivery doc's ID (1:1 relationship, same
    # trick used for payments/payouts).
    delivery_ref = db.collection("deliveries").document(order_id)
    delivery_snap = await delivery_ref.get()
    if delivery_snap.exists:
        await delivery_ref.update({"fargo_tracking_id": body.fargoTrackingId, "proof_of_shipment_url": body.proofOfShipmentUrl})
    else:
        await delivery_ref.set({
            "order_id": order_id, "fargo_tracking_id": body.fargoTrackingId,
            "proof_of_shipment_url": body.proofOfShipmentUrl, "status": "awaiting_pickup",
            "attempts": 0, "last_status_at": None, "raw_webhook_payload": None, "created_at": now(),
        })

    await mark_shipped(db, order_id)
    return {"ok": True}


@router.post("/orders/{order_id}/confirm-receipt")
async def confirm_receipt(order_id: str, user: dict = Depends(get_current_user), db=Depends(get_db)):
    order_snap = await db.collection("orders").document(order_id).get()
    if not order_snap.exists or order_snap.to_dict().get("customer_id") != user["id"]:
        raise HTTPException(status_code=403, detail="Not your order")
    try:
        await release_payout(db, order_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.post("/orders/{order_id}/dispute")
async def raise_dispute(order_id: str, body: DisputeRequest, user: dict = Depends(get_current_user), db=Depends(get_db)):
    order_ref = db.collection("orders").document(order_id)
    order_snap = await order_ref.get()
    if not order_snap.exists or order_snap.to_dict().get("customer_id") != user["id"]:
        raise HTTPException(status_code=403, detail="Not your order")

    dispute_id = new_id()
    await db.collection("disputes").document(dispute_id).set({
        "order_id": order_id, "raised_by": user["id"], "reason": body.reason,
        "description": body.description, "status": "open",
        "resolved_by": None, "resolved_at": None, "created_at": now(),
    })
    await order_ref.update({"status": "disputed"})
    return {"ok": True}
