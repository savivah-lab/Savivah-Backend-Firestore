"""
Same rule as before: the client only ever submits product IDs and
quantities — the backend re-reads current prices/stock and calculates the
authoritative total. The frontend never gets to define the price (the
request schema doesn't even have a price field).

The stock-locking mechanism is different from Postgres, but the GUARANTEE
is the same: a Firestore transaction here reads each product's current
stock, validates it, and writes the decrement — if another checkout
changes that same product between the read and the commit, Firestore
detects the conflict and automatically retries the whole transaction from
scratch. Two people can never both buy the last unit. This is optimistic
concurrency (retry-on-conflict) rather than Postgres's pessimistic
SELECT ... FOR UPDATE (block-until-available) — different mechanism,
same real-world outcome.
"""
import time
from fastapi import APIRouter, Depends, HTTPException
from google.cloud.firestore import async_transactional
from app.core.firestore_client import get_db
from app.deps import get_current_user
from app.services.firestore_helpers import new_id, now
from app.services import pesapal
from app.services.escrow import COMMISSION_RATE
from app.schemas.order import CheckoutRequest, CheckoutResponse

router = APIRouter(prefix="/api", tags=["checkout"])


@router.post("/checkout", response_model=CheckoutResponse)
async def checkout(body: CheckoutRequest, user: dict = Depends(get_current_user), db=Depends(get_db)):
    order_id = new_id()

    @async_transactional
    async def do_checkout(transaction):
        store_snap = await db.collection("stores").document(str(body.storeId)).get(transaction=transaction)
        if not store_snap.exists:
            raise HTTPException(status_code=400, detail="Store not found")
        store_name = store_snap.to_dict()["name"]

        subtotal = 0.0
        line_items = []
        product_refs = []

        # All reads must happen before any writes in a Firestore transaction.
        for item in body.items:
            ref = db.collection("products").document(str(item.productId))
            snap = await ref.get(transaction=transaction)
            if not snap.exists or snap.to_dict().get("store_id") != str(body.storeId):
                raise HTTPException(status_code=400, detail=f"Product {item.productId} not found in this store")
            product = snap.to_dict()
            if product["stock"] < item.quantity:
                raise HTTPException(status_code=400, detail=f"Insufficient stock for {product['name']}")
            subtotal += float(product["price"]) * item.quantity
            line_items.append((ref, product, item.quantity))
            product_refs.append(ref)

        commission = round(subtotal * COMMISSION_RATE, 2)
        payout = round(subtotal - commission, 2)

        order_doc = {
            "customer_id": user["id"], "store_id": str(body.storeId), "store_name": store_name, "subtotal": subtotal,
            "commission_rate": COMMISSION_RATE, "commission_amount": commission, "payout_amount": payout,
            "currency": "KES", "status": "pending_payment", "delivery_address": body.deliveryAddress,
            "shipped_at": None, "delivered_at": None, "payout_released_at": None, "auto_release_at": None,
            "created_at": now(),
            "items": [
                {"product_id": ref.id, "product_name": p["name"], "unit_price": p["price"], "quantity": qty}
                for ref, p, qty in line_items
            ],
        }
        transaction.set(db.collection("orders").document(order_id), order_doc)

        for ref, product, qty in line_items:
            transaction.update(ref, {"stock": product["stock"] - qty})

        return subtotal

    subtotal = await do_checkout(db.transaction())

    merchant_reference = f"SVH-{order_id[:8]}-{int(time.time() * 1000)}"
    payment_doc = {
        "order_id": order_id, "pesapal_order_tracking_id": None,
        "pesapal_merchant_reference": merchant_reference, "amount": subtotal,
        "payment_method": None, "status_code": None, "status_description": None,
        "confirmation_code": None, "raw_ipn_payload": None, "created_at": now(), "updated_at": now(),
    }
    # order_id doubles as the payment doc's ID (one payment per order today).
    await db.collection("payments").document(order_id).set(payment_doc)

    first_name, *rest = (user.get("full_name") or "Savivah Customer").split(" ")
    last_name = " ".join(rest) or first_name

    try:
        pesapal_order = await pesapal.submit_order_request(
            merchant_reference=merchant_reference, amount=subtotal,
            description=f"Savivah order {order_id[:8]}",
            email=user["email"], phone=user.get("phone_number"), first_name=first_name, last_name=last_name,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Payment provider error: {e}")

    await db.collection("payments").document(order_id).update({
        "pesapal_order_tracking_id": pesapal_order["order_tracking_id"],
    })

    return CheckoutResponse(orderId=order_id, redirectUrl=pesapal_order["redirect_url"])
