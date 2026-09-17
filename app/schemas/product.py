from pydantic import BaseModel, Field
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

    # All product images.
    # Existing products without image_urls will still work because
    # the backend will fall back to image_url.
    image_urls: list[str] = Field(default_factory=list)

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

    # Kept for backward compatibility with the current frontend.
    imageUrl: str | None = None

    # New multi-image field.
    imageUrls: list[str] = Field(default_factory=list)


class ProductUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    price: float | None = None
    stock: int | None = None

    # Backward-compatible single image field.
    imageUrl: str | None = None

    # New multi-image field.
    # None = don't change the images.
    # [] = remove all images.
    imageUrls: list[str] | None = None

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
