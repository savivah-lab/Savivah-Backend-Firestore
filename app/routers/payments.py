from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from app.core.firestore_client import get_db
from app.core.config import settings
from app.services import pesapal
from app.services.escrow import confirm_payment_escrow

router = APIRouter(prefix="/api/payments", tags=["payments"])


@router.get("/callback")
async def payment_callback(OrderTrackingId: str, OrderMerchantReference: str, db=Depends(get_db)):
    try:
        status = await pesapal.get_transaction_status(OrderTrackingId)
        if status.get("status_code") == 1:
            docs = [d async for d in db.collection("payments").where("pesapal_order_tracking_id", "==", OrderTrackingId).limit(1).stream()]
            if docs:
                await confirm_payment_escrow(db, docs[0].id)  # payment doc ID == order_id
        description = status.get("payment_status_description", "unknown")
    except Exception:
        description = "error"
    return RedirectResponse(f"{settings.FRONTEND_URL}/orders?payment={description}")


@router.post("/ipn")
async def payment_ipn(request: Request, db=Depends(get_db)):
    body = await request.json()
    order_tracking_id = body.get("OrderTrackingId")
    merchant_reference = body.get("OrderMerchantReference")

    try:
        status = await pesapal.get_transaction_status(order_tracking_id)
        docs = [d async for d in db.collection("payments").where("pesapal_order_tracking_id", "==", order_tracking_id).limit(1).stream()]

        if docs:
            order_id = docs[0].id  # payment doc ID == order_id
            # Idempotent: writing the same status twice is harmless, and
            # confirm_payment_escrow only ever transitions from
            # pending_payment, so a repeated IPN can't double-fire it.
            await db.collection("payments").document(order_id).update({
                "status_code": status.get("status_code"),
                "status_description": status.get("payment_status_description"),
                "payment_method": status.get("payment_method"),
                "confirmation_code": status.get("confirmation_code"),
                "raw_ipn_payload": body,
            })
            if status.get("status_code") == 1:
                await confirm_payment_escrow(db, order_id)

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
