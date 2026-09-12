"""
Every aggregation here (sums, counts, per-store grouping) is done in Python
after streaming matching documents — Firestore has no GROUP BY or JOIN.
This is fine at the order volumes a new marketplace actually has, but it's
a real scaling ceiling worth knowing about: at high volume, the idiomatic
Firestore fix is maintaining running counters (e.g. an `aggregates/stats`
document incremented on every order write) rather than summing on every
read. Not built here — flagging it so it's a deliberate future decision,
not a surprise.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from app.core.firestore_client import get_db
from app.deps import get_current_admin
from app.services.firestore_helpers import now
from app.services.escrow import release_payout, refund_order
from app.schemas.admin import AdminStats, SellerSummary, PayoutOut, DisputeResolveRequest

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(get_current_admin)])


@router.get("/stats", response_model=AdminStats)
async def stats(db=Depends(get_db)):
    commission_earned = 0.0
    async for d in db.collection("orders").where("status", "==", "delivered").stream():
        commission_earned += float(d.to_dict().get("commission_amount", 0))

    in_escrow = 0.0
    for s in ("escrow_held", "shipped"):
        async for d in db.collection("orders").where("status", "==", s).stream():
            in_escrow += float(d.to_dict().get("subtotal", 0))

    total_orders = 0
    async for _ in db.collection("orders").select([]).stream():  # projection query — cheaper than pulling full docs
        total_orders += 1

    return AdminStats(commission_earned=commission_earned, in_escrow=in_escrow, total_orders=total_orders)


@router.get("/orders")
async def all_orders(limit: int = Query(default=200, le=500), db=Depends(get_db)):
    query = db.collection("orders").order_by("created_at", direction="DESCENDING").limit(limit)
    out = []
    async for d in query.stream():
        o = d.to_dict()
        o["id"] = d.id
        out.append(o)
    return out


@router.get("/sellers", response_model=list[SellerSummary])
async def sellers(db=Depends(get_db)):
    stores = [d async for d in db.collection("stores").order_by("created_at", direction="DESCENDING").stream()]

    # One query per store for its orders — an N+1 pattern, acceptable at the
    # store counts a young marketplace has; would need the counter pattern
    # described above if this ever becomes hundreds of stores.
    out = []
    for store_doc in stores:
        store = store_doc.to_dict()
        owner_snap = await db.collection("users").document(store["owner_id"]).get()
        owner = owner_snap.to_dict() if owner_snap.exists else {"full_name": "Unknown", "email": "—"}

        pending_escrow = 0.0
        total_earned = 0.0
        total_orders = 0
        async for od in db.collection("orders").where("store_id", "==", store_doc.id).stream():
            o = od.to_dict()
            total_orders += 1
            if o["status"] in ("escrow_held", "shipped"):
                pending_escrow += float(o.get("payout_amount", 0))
            elif o["status"] == "delivered":
                total_earned += float(o.get("payout_amount", 0))

        out.append(SellerSummary(
            id=store_doc.id, name=store["name"], verified=store.get("verified", False),
            owner_name=owner.get("full_name", "Unknown"), owner_email=owner.get("email", "—"),
            pending_escrow=pending_escrow, total_earned=total_earned, total_orders=total_orders,
        ))
    return out


@router.get("/payouts", response_model=list[PayoutOut])
async def payouts(status: str | None = Query(default=None), db=Depends(get_db)):
    query = db.collection("payouts").order_by("created_at", direction="DESCENDING").limit(200)
    if status:
        query = db.collection("payouts").where("status", "==", status).order_by("created_at", direction="DESCENDING").limit(200)

    out = []
    async for d in query.stream():
        p = d.to_dict()
        store_snap = await db.collection("stores").document(p["store_id"]).get()
        store_name = store_snap.to_dict()["name"] if store_snap.exists else "Unknown store"
        out.append(PayoutOut(id=d.id, store_id=p["store_id"], store_name=store_name,
                                amount=float(p["amount"]), method=p.get("method"), status=p["status"]))
    return out


@router.post("/payouts/{payout_id}/mark-sent", response_model=PayoutOut)
async def mark_payout_sent(payout_id: str, admin: dict = Depends(get_current_admin), db=Depends(get_db)):
    ref = db.collection("payouts").document(payout_id)
    snap = await ref.get()
    if not snap.exists or snap.to_dict().get("status") != "pending":
        raise HTTPException(status_code=404, detail="No pending payout found with that id")

    await ref.update({"status": "sent", "sent_at": now(), "dispatched_by": admin["id"]})  # audit trail
    p = snap.to_dict()
    store_snap = await db.collection("stores").document(p["store_id"]).get()
    store_name = store_snap.to_dict()["name"] if store_snap.exists else "Unknown store"
    return PayoutOut(id=payout_id, store_id=p["store_id"], store_name=store_name,
                        amount=float(p["amount"]), method=p.get("method"), status="sent")


@router.get("/disputes")
async def open_disputes(db=Depends(get_db)):
    query = db.collection("disputes").where("status", "==", "open").order_by("created_at", direction="ASCENDING")
    out = []
    async for d in query.stream():
        item = d.to_dict()
        item["id"] = d.id
        out.append(item)
    return out


@router.post("/disputes/{dispute_id}/resolve")
async def resolve_dispute(dispute_id: str, body: DisputeResolveRequest, admin: dict = Depends(get_current_admin), db=Depends(get_db)):
    ref = db.collection("disputes").document(dispute_id)
    snap = await ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Dispute not found")
    dispute = snap.to_dict()

    status_map = {"refund": "resolved_refund", "release": "resolved_release", "reject": "rejected"}
    if body.resolution not in status_map:
        raise HTTPException(status_code=400, detail="resolution must be one of: refund, release, reject")

    await ref.update({"status": status_map[body.resolution], "resolved_by": admin["id"], "resolved_at": now()})  # audit trail

    if body.resolution == "refund":
        await refund_order(db, dispute["order_id"])
    else:
        await release_payout(db, dispute["order_id"])

    return {"ok": True}
