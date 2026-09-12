import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_db
from app.deps import get_current_admin
from app.models.user import User, AdminUser
from app.models.store import Store
from app.models.order import Order
from app.models.payout import Payout
from app.models.dispute import Dispute
from app.schemas.admin import AdminStats, SellerSummary, PayoutOut, DisputeResolveRequest
from app.services.escrow import release_payout, refund_order

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(get_current_admin)])


@router.get("/stats", response_model=AdminStats)
async def stats(db: AsyncSession = Depends(get_db)):
    commission = (await db.execute(
        select(func.coalesce(func.sum(Order.commission_amount), 0)).where(Order.status == "delivered")
    )).scalar_one()
    in_escrow = (await db.execute(
        select(func.coalesce(func.sum(Order.subtotal), 0)).where(Order.status.in_(["escrow_held", "shipped"]))
    )).scalar_one()
    total = (await db.execute(select(func.count(Order.id)))).scalar_one()
    return AdminStats(commission_earned=float(commission), in_escrow=float(in_escrow), total_orders=total)


@router.get("/orders")
async def all_orders(limit: int = Query(default=200, le=500), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(Order, Store.name.label("store_name"))
        .join(Store, Store.id == Order.store_id)
        .order_by(Order.created_at.desc())
        .limit(limit)
    )).all()
    return [{**o.__dict__, "store_name": name} for o, name in rows]


@router.get("/sellers", response_model=list[SellerSummary])
async def sellers(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(
            Store, User.full_name, User.email,
            func.coalesce(func.sum(Order.payout_amount).filter(Order.status.in_(["escrow_held", "shipped"])), 0),
            func.coalesce(func.sum(Order.payout_amount).filter(Order.status == "delivered"), 0),
            func.count(Order.id),
        )
        .join(User, User.id == Store.owner_id)
        .outerjoin(Order, Order.store_id == Store.id)
        .group_by(Store.id, User.full_name, User.email)
        .order_by(Store.created_at.desc())
    )).all()
    return [
        SellerSummary(
            id=store.id, name=store.name, verified=store.verified,
            owner_name=owner_name, owner_email=owner_email,
            pending_escrow=float(pending), total_earned=float(earned), total_orders=count,
        )
        for store, owner_name, owner_email, pending, earned, count in rows
    ]


@router.get("/payouts", response_model=list[PayoutOut])
async def payouts(status: str | None = Query(default=None), db: AsyncSession = Depends(get_db)):
    query = select(Payout, Store.name.label("store_name")).join(Store, Store.id == Payout.store_id)
    if status:
        query = query.where(Payout.status == status)
    rows = (await db.execute(query.order_by(Payout.created_at.desc()).limit(200))).all()
    return [
        PayoutOut(id=p.id, store_id=p.store_id, store_name=store_name, amount=float(p.amount),
                    method=p.method, status=p.status)
        for p, store_name in rows
    ]


@router.post("/payouts/{payout_id}/mark-sent", response_model=PayoutOut)
async def mark_payout_sent(payout_id: uuid.UUID, admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    """Strong authorization (admin-only route) + audit log: records exactly
    which admin dispatched this payout and when — per the spec's requirement
    for sensitive financial actions."""
    payout = await db.get(Payout, payout_id)
    if not payout or payout.status != "pending":
        raise HTTPException(status_code=404, detail="No pending payout found with that id")
    payout.status = "sent"
    payout.sent_at = datetime.now(timezone.utc)
    payout.dispatched_by = admin.id  # audit trail
    await db.commit()
    store = await db.get(Store, payout.store_id)
    await db.refresh(payout)
    return PayoutOut(id=payout.id, store_id=payout.store_id, store_name=store.name,
                        amount=float(payout.amount), method=payout.method, status=payout.status)


@router.get("/disputes")
async def open_disputes(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Dispute).where(Dispute.status == "open").order_by(Dispute.created_at.asc()))).scalars().all()
    return rows


@router.post("/disputes/{dispute_id}/resolve")
async def resolve_dispute(
    dispute_id: uuid.UUID, body: DisputeResolveRequest,
    admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db),
):
    dispute = await db.get(Dispute, dispute_id)
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")

    status_map = {"refund": "resolved_refund", "release": "resolved_release", "reject": "rejected"}
    if body.resolution not in status_map:
        raise HTTPException(status_code=400, detail="resolution must be one of: refund, release, reject")

    dispute.status = status_map[body.resolution]
    dispute.resolved_by = admin.id  # audit trail
    dispute.resolved_at = datetime.now(timezone.utc)
    await db.commit()

    if body.resolution == "refund":
        await refund_order(db, dispute.order_id)
    else:
        await release_payout(db, dispute.order_id)

    return {"ok": True}
