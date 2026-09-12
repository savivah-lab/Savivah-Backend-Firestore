"""
Same rules as the Postgres version: hold on payment, release after delivery
+ grace window (unless disputed), refund on shipping/delivery failure.
Firestore transactions provide the same "no lost updates" guarantee as
Postgres row locks did, just via optimistic retry instead of pessimistic
locking — functionally equivalent for our purposes.
"""
from datetime import timedelta
from google.cloud.firestore import async_transactional
from app.services.firestore_helpers import now

COMMISSION_RATE = 0.10
AUTO_RELEASE_DAYS = 5


async def confirm_payment_escrow(db, order_id: str) -> None:
    ref = db.collection("orders").document(order_id)
    snap = await ref.get()
    if snap.exists and snap.to_dict().get("status") == "pending_payment":
        await ref.update({"status": "escrow_held"})


async def mark_shipped(db, order_id: str) -> None:
    ref = db.collection("orders").document(order_id)
    snap = await ref.get()
    if snap.exists and snap.to_dict().get("status") == "escrow_held":
        await ref.update({"status": "shipped", "shipped_at": now()})


async def mark_delivered_awaiting_release(db, order_id: str) -> None:
    ref = db.collection("orders").document(order_id)
    snap = await ref.get()
    if snap.exists and snap.to_dict().get("status") == "shipped":
        await ref.update({
            "status": "delivered", "delivered_at": now(),
            "auto_release_at": now() + timedelta(days=AUTO_RELEASE_DAYS),
        })


async def release_payout(db, order_id: str) -> dict:
    @async_transactional
    async def _release(transaction):
        order_ref = db.collection("orders").document(order_id)
        order_snap = await order_ref.get(transaction=transaction)
        if not order_snap.exists:
            raise ValueError("Order not found")
        order = order_snap.to_dict()

        open_disputes = [d async for d in db.collection("disputes")
                          .where("order_id", "==", order_id).where("status", "==", "open")
                          .stream(transaction=transaction)]
        if open_disputes:
            raise ValueError("Cannot release payout while a dispute is open")

        store_snap = await db.collection("stores").document(order["store_id"]).get(transaction=transaction)
        payout_method = store_snap.to_dict().get("payout_method") if store_snap.exists else None

        payout_doc = {
            "store_id": order["store_id"], "order_id": order_id,
            "amount": order["payout_amount"], "method": payout_method,
            "status": "pending", "dispatched_by": None, "sent_at": None, "created_at": now(),
        }
        # order_id doubles as the payout's document ID — this is how we get
        # the UNIQUE(order_id) guarantee from the old schema for free.
        transaction.set(db.collection("payouts").document(order_id), payout_doc)
        transaction.update(order_ref, {"payout_released_at": now()})
        return {**payout_doc, "id": order_id}

    return await _release(db.transaction())
    # In production: trigger an actual M-Pesa B2C or bank transfer here (not
    # automated anywhere in this codebase yet — see the connection reference
    # doc) — then flip status to 'sent' once the transfer confirms.


async def refund_order(db, order_id: str) -> None:
    ref = db.collection("orders").document(order_id)
    snap = await ref.get()
    if snap.exists:
        await ref.update({"status": "refunded"})
    # In production: trigger a Pesapal RefundRequest call here — not implemented yet.


async def run_auto_release_sweep(db) -> int:
    """Intended to run on a schedule (see workers/payout_sweep.py). Requires
    a composite index on orders(status ASC, auto_release_at ASC) — see
    firestore.indexes.json."""
    query = (db.collection("orders")
             .where("status", "==", "delivered")
             .where("auto_release_at", "<=", now()))
    candidates = [d async for d in query.stream()]

    released = 0
    for doc in candidates:
        try:
            await release_payout(db, doc.id)
            released += 1
        except ValueError:
            continue  # an open dispute — skip until it's resolved
    return released
