from pydantic import BaseModel
import uuid


class CheckoutItem(BaseModel):
    productId: uuid.UUID
    quantity: int


class CheckoutRequest(BaseModel):
    storeId: uuid.UUID
    items: list[CheckoutItem]
    deliveryAddress: str


class CheckoutResponse(BaseModel):
    orderId: uuid.UUID
    redirectUrl: str


class ShipRequest(BaseModel):
    fargoTrackingId: str
    proofOfShipmentUrl: str


class DisputeRequest(BaseModel):
    reason: str  # not_delivered | item_not_as_described | damaged | other
    description: str | None = None


class FargoWebhookPayload(BaseModel):
    """
    ASSUMPTION, not confirmed against real Fargo docs — see the connection
    reference document, section 6. This is the single most likely schema
    to need changing once real Fargo API access is available.
    """
    fargo_tracking_id: str
    status: str  # "in_transit" | "delivered" | "failed"
