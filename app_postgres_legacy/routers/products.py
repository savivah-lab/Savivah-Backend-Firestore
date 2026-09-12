"""
The public marketplace read path: Browser -> FastAPI -> Redis -> (miss) ->
PostgreSQL -> Redis -> Response. Bounded pages only — the frontend must
never be able to request the entire catalogue in one call.
"""
import base64
import uuid
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_db
from app.core.redis_client import get_redis
from app.models.product import Product
from app.models.store import Store
from app.schemas.product import ProductOut, ProductPage
from app.services.cache import get_cached_products, set_cached_products

router = APIRouter(prefix="/api/products", tags=["products"])

DEFAULT_PAGE_SIZE = 24
MAX_PAGE_SIZE = 60


def _encode_cursor(created_at, product_id: uuid.UUID) -> str:
    raw = f"{created_at.isoformat()}|{product_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[str, str]:
    raw = base64.urlsafe_b64decode(cursor.encode()).decode()
    created_at, product_id = raw.split("|", 1)
    return created_at, product_id


@router.get("", response_model=ProductPage)
async def list_products(
    search: str | None = Query(default=None),
    category: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, le=MAX_PAGE_SIZE),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    cached = await get_cached_products(redis, search, category, cursor, limit)
    if cached:
        return ProductPage(**cached)

    query = (
        select(Product, Store.name.label("store_name"), Store.verified.label("store_verified"))
        .join(Store, Store.id == Product.store_id)
        .where(Product.status == "active")
        .order_by(Product.created_at.desc(), Product.id.desc())
        .limit(limit + 1)  # fetch one extra to know if there's a next page
    )
    if search:
        query = query.where(Product.name.ilike(f"%{search}%"))
    if category:
        query = query.where(Product.category == category)
    if cursor:
        created_at, product_id = _decode_cursor(cursor)
        query = query.where(Product.created_at < created_at)

    rows = (await db.execute(query)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    items = [
        ProductOut(
            id=p.id, store_id=p.store_id, store_name=store_name, store_verified=store_verified,
            name=p.name, description=p.description, category=p.category,
            price=float(p.price), stock=p.stock, image_url=p.image_url, status=p.status,
        )
        for p, store_name, store_verified in rows
    ]
    next_cursor = _encode_cursor(rows[-1][0].created_at, rows[-1][0].id) if has_more and rows else None

    page = ProductPage(items=items, next_cursor=next_cursor)
    await set_cached_products(redis, search, category, cursor, limit, page.model_dump(mode="json"))
    return page
