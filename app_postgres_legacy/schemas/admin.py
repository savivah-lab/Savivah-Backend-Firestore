from pydantic import BaseModel
import uuid


class AdminStats(BaseModel):
    commission_earned: float
    in_escrow: float
    total_orders: int


class SellerSummary(BaseModel):
    id: uuid.UUID
    name: str
    verified: bool
    owner_name: str
    owner_email: str
    pending_escrow: float
    total_earned: float
    total_orders: int


class PayoutOut(BaseModel):
    id: uuid.UUID
    store_id: uuid.UUID
    store_name: str
    amount: float
    method: str | None = None
    payout_account: str | None = None
    status: str

    class Config:
        from_attributes = True


class DisputeResolveRequest(BaseModel):
    resolution: str  # "refund" | "release" | "reject"
