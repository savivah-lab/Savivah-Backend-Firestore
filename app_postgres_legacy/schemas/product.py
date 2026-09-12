from pydantic import BaseModel
import uuid


class ProductOut(BaseModel):
    id: uuid.UUID
    store_id: uuid.UUID
    store_name: str | None = None
    store_verified: bool | None = None
    name: str
    description: str | None = None
    category: str | None = None
    price: float
    stock: int
    image_url: str | None = None
    status: str

    class Config:
        from_attributes = True


class ProductPage(BaseModel):
    """Cursor-paginated product list — the frontend requests a bounded page,
    never the full catalogue, per the architecture spec."""
    items: list[ProductOut]
    next_cursor: str | None = None


class ProductCreateRequest(BaseModel):
    name: str
    description: str | None = None
    category: str | None = None
    price: float
    stock: int
    imageUrl: str | None = None


class ProductUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    price: float | None = None
    stock: int | None = None
    imageUrl: str | None = None
    status: str | None = None


class StoreCreateRequest(BaseModel):
    name: str
    businessRegNumber: str | None = None
    payoutMethod: str | None = None
    payoutAccount: str | None = None


class StoreOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    verified: bool
    payout_method: str | None = None
    payout_account: str | None = None

    class Config:
        from_attributes = True
