"""
Redis connection. Per the architecture decision: Redis accelerates reads
and holds rate-limit counters — it is never the source of truth for stock,
orders, payments, or payouts. Nothing in this file (or anywhere else) should
write financial or inventory state to Redis as its only copy.
"""
import redis.asyncio as redis
from app.core.config import settings

redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)


async def get_redis():
    return redis_client
