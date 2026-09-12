"""
Pesapal notifies us two ways (see the connection reference doc, section 3,
step 4) — both handled here. Both are idempotent: processing the same
notification twice must never double-confirm an order or create duplicate
state, since Pesapal explicitly can and does retry/repeat these calls.
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_db
from app.core.config import settings
from app.models.payment import Payment
from app.services import pesapal
from app.services.escrow import confirm_payment_escrow

router = APIRouter(prefix="/api/payments", tags=["payments"])


@router.get("/callback")
async def payment_callback(OrderTrackingId: str, OrderMerchantReference: str, db: AsyncSession = Depends(get_db)):
    """Best-effort — the browser redirect can be lost if the customer closes
    the tab. The IPN below is the reliable path. Per Pesapal's own docs, the
    query params here never carry the actual payment status — always
    re-verify via GetTransactionStatus."""
    try:
        status = await pesapal.get_transaction_status(OrderTrackingId)
        if status.get("status_code") == 1:
            payment = (await db.execute(
                select(Payment).where(Payment.pesapal_order_tracking_id == OrderTrackingId)
            )).scalar_one_or_none()
            if payment:
                await confirm_payment_escrow(db, payment.order_id)
        description = status.get("payment_status_description", "unknown")
    except Exception:
        description = "error"
    return RedirectResponse(f"{settings.FRONTEND_URL}/orders?payment={description}")


@router.post("/ipn")
async def payment_ipn(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    order_tracking_id = body.get("OrderTrackingId")
    merchant_reference = body.get("OrderMerchantReference")

    try:
        status = await pesapal.get_transaction_status(order_tracking_id)
        payment = (await db.execute(
            select(Payment).where(Payment.pesapal_order_tracking_id == order_tracking_id)
        )).scalar_one_or_none()

        if payment:
            # Idempotent: writing the same status twice is harmless, and we
            # only ever transition escrow_held from pending_payment (see
            # confirm_payment_escrow), so a repeated IPN can't double-fire it.
            payment.status_code = status.get("status_code")
            payment.status_description = status.get("payment_status_description")
            payment.payment_method = status.get("payment_method")
            payment.confirmation_code = status.get("confirmation_code")
            payment.raw_ipn_payload = body
            await db.commit()

            if status.get("status_code") == 1:
                await confirm_payment_escrow(db, payment.order_id)

        # Pesapal requires this exact response shape to acknowledge the IPN.
        return {
            "orderNotificationType": "IPNCHANGE",
            "orderTrackingId": order_tracking_id,
            "orderMerchantReference": merchant_reference,
            "status": 200,
        }
    except Exception:
        return {
            "orderNotificationType": "IPNCHANGE",
            "orderTrackingId": order_tracking_id,
            "orderMerchantReference": merchant_reference,
            "status": 500,
        }
