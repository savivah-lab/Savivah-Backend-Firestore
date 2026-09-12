"""
A small Redis-backed rate limiter, used on login/register endpoints to
blunt brute-force and credential-stuffing attempts. This is one layer of
the traffic-protection model — it does not replace a CDN/WAF in front of
the whole app, only protects specific sensitive endpoints at the
application layer.
"""
from fastapi import Request, HTTPException, Depends
from app.core.redis_client import get_redis
from app.core.config import settings


def rate_limit(key_prefix: str, limit: int | None = None):
    """Returns a FastAPI dependency that rate-limits by client IP."""

    async def _check(request: Request, redis=Depends(get_redis)):
        max_requests = limit or settings.LOGIN_RATE_LIMIT_PER_MINUTE
        client_ip = request.client.host if request.client else "unknown"
        key = f"ratelimit:{key_prefix}:{client_ip}"
        current = await redis.incr(key)
        if current == 1:
            await redis.expire(key, 60)
        if current > max_requests:
            raise HTTPException(status_code=429, detail="Too many requests. Please slow down and try again shortly.")

    return _check
