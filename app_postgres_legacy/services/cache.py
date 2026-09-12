"""
Read-through cache for product listing pages. PostgreSQL is always queried
on a cache miss and remains the only source of truth — Redis here only
saves repeat reads of the exact same page from hitting the database.

Cache keys include every dimension that affects the response (search term,
category, cursor) so two different queries can never collide.
"""
import json
from app.core.config import settings


def _cache_key(search: str | None, category: str | None, cursor: str | None, limit: int) -> str:
    return f"products:list:{search or ''}:{category or ''}:{cursor or ''}:{limit}"


async def get_cached_products(redis, search, category, cursor, limit) -> dict | None:
    raw = await redis.get(_cache_key(search, category, cursor, limit))
    return json.loads(raw) if raw else None


async def set_cached_products(redis, search, category, cursor, limit, payload: dict) -> None:
    await redis.set(
        _cache_key(search, category, cursor, limit),
        json.dumps(payload, default=str),
        ex=settings.PRODUCT_CACHE_TTL_SECONDS,
    )


async def invalidate_product_cache(redis) -> None:
    """
    Called after any product create/update. A short TTL (see
    PRODUCT_CACHE_TTL_SECONDS) already bounds staleness even if this is
    missed somewhere, but explicit invalidation keeps changes visible
    immediately rather than waiting out the TTL.
    """
    async for key in redis.scan_iter("products:list:*"):
        await redis.delete(key)
