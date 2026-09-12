"""
Checkout is a transactional backend operation, per the architecture spec:
the client only ever submits product IDs and quantities — the backend
re-reads current prices and stock from PostgreSQL and calculates the
authoritative order total. The frontend-submitted price, if any were ever
sent, is ignored entirely (this schema doesn't even accept one — see
CheckoutRequest in schemas/order.py).
"""
import time
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_db
from app.deps import get_current_user
from app.models.user import User
from app.models.product import Product
from app.models.order import Order, OrderItem
from app.models.payment import Payment
from app.schemas.order import CheckoutRequest, CheckoutResponse
from app.services import pesapal
from app.services.escrow import COMMISSION_RATE

router = APIRouter(prefix="/api", tags=["checkout"])


@router.post("/checkout", response_model=CheckoutResponse)
async def checkout(
    body: CheckoutRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Step 3-5 of the DB transaction: load current prices, validate stock,
    # decrement atomically. SELECT ... FOR UPDATE locks each product row for
    # the duration of this transaction so two simultaneous checkouts can
    # never both oversell the same last unit.
    subtotal = 0.0
    line_items = []
    for item in body.items:
        product = (await db.execute(
            select(Product).where(Product.id == item.productId, Product.store_id == body.storeId).with_for_update()
        )).scalar_one_or_none()
        if not product:
            raise HTTPException(status_code=400, detail=f"Product {item.productId} not found in this store")
        if product.stock < item.quantity:
            raise HTTPException(status_code=400, detail=f"Insufficient stock for {product.name}")
        subtotal += float(product.price) * item.quantity
        line_items.append((product, item.quantity))

    commission = round(subtotal * COMMISSION_RATE, 2)
    payout = round(subtotal - commission, 2)

    order = Order(
        customer_id=user.id, store_id=body.storeId, subtotal=subtotal,
        commission_rate=COMMISSION_RATE, commission_amount=commission, payout_amount=payout,
        delivery_address=body.deliveryAddress,
    )
    db.add(order)
    await db.flush()  # get order.id without committing yet

    for product, qty in line_items:
        db.add(OrderItem(
            order_id=order.id, product_id=product.id, product_name=product.name,
            unit_price=product.price, quantity=qty,
        ))
        product.stock -= qty

    merchant_reference = f"SVH-{str(order.id)[:8]}-{int(time.time() * 1000)}"
    db.add(Payment(order_id=order.id, pesapal_merchant_reference=merchant_reference, amount=subtotal))

    await db.commit()  # commit BEFORE calling Pesapal — an outage there must
                        # never leave half-written order data (same rule as before)

    # Look up the customer's real phone/name for the Pesapal billing address —
    # the JWT only ever carries { sub, role, email }, never phone or name.
    first_name, *rest = (user.full_name or "Savivah Customer").split(" ")
    last_name = " ".join(rest) or first_name

    try:
        pesapal_order = await pesapal.submit_order_request(
            merchant_reference=merchant_reference, amount=subtotal,
            description=f"Savivah order {str(order.id)[:8]}",
            email=user.email, phone=user.phone_number, first_name=first_name, last_name=last_name,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Payment provider error: {e}")

    payment = (await db.execute(select(Payment).where(Payment.pesapal_merchant_reference == merchant_reference))).scalar_one()
    payment.pesapal_order_tracking_id = pesapal_order["order_tracking_id"]
    await db.commit()

    return CheckoutResponse(orderId=order.id, redirectUrl=pesapal_order["redirect_url"])
