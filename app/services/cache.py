"""
Read-through cache for product listing pages.

Firestore remains the source of truth. Redis only caches repeat reads
of the exact same product-list query.

The cache key includes every parameter that can affect the response,
so different searches, filters, sorting options, and pagination cursors
cannot collide.
"""
import json
from app.core.config import settings


def _cache_key(
    search: str | None,
    category: str | None,
    cursor: str | None,
    limit: int,
    min_price: float | None = None,
    max_price: float | None = None,
    in_stock: bool = False,
    verified_seller: bool = False,
    sort: str = "relevance",
) -> str:
    return (
        f"products:list:"
        f"{search or ''}:"
        f"{category or ''}:"
        f"{cursor or ''}:"
        f"{limit}:"
        f"{min_price if min_price is not None else ''}:"
        f"{max_price if max_price is not None else ''}:"
        f"{int(in_stock)}:"
        f"{int(verified_seller)}:"
        f"{sort or 'relevance'}"
    )


async def get_cached_products(
    redis,
    search,
    category,
    cursor,
    limit,
    min_price=None,
    max_price=None,
    in_stock=False,
    verified_seller=False,
    sort="relevance",
) -> dict | None:
    raw = await redis.get(
        _cache_key(
            search,
            category,
            cursor,
            limit,
            min_price,
            max_price,
            in_stock,
            verified_seller,
            sort,
        )
    )

    return json.loads(raw) if raw else None


async def set_cached_products(
    redis,
    search,
    category,
    cursor,
    limit,
    payload: dict,
    min_price=None,
    max_price=None,
    in_stock=False,
    verified_seller=False,
    sort="relevance",
) -> None:
    await redis.set(
        _cache_key(
            search,
            category,
            cursor,
            limit,
            min_price,
            max_price,
            in_stock,
            verified_seller,
            sort,
        ),
        json.dumps(payload, default=str),
        ex=settings.PRODUCT_CACHE_TTL_SECONDS,
    )


async def invalidate_product_cache(redis) -> None:
    """
    Called after any product create/update.

    Explicit invalidation keeps product changes visible immediately
    instead of waiting for the cache TTL to expire.
    """
    async for key in redis.scan_iter("products:list:*"):
        await redis.delete(key)
