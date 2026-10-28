"""Redis-backed rate limiter for authentication endpoints."""

from __future__ import annotations

import redis.asyncio as redis
from fastapi import Request

from app.config import get_settings
from app.errors import RateLimitError

_redis_client: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


async def close_rate_limiter() -> None:
    global _redis_client
    if _redis_client:
        await _redis_client.aclose()
        _redis_client = None


async def check_rate_limit(
    key: str,
    *,
    max_attempts: int,
    window_seconds: int,
) -> None:
    """Increment a rate-limit counter and raise if limit exceeded.

    Uses a sliding window counter in Redis with automatic expiry.
    """
    settings = get_settings()
    if settings.app_env.value == "testing":
        # Unit tests run without Redis; rate limiting is exercised in e2e.
        return

    r = _get_redis()
    redis_key = f"rate_limit:{key}"

    # INCR and TTL go in one transaction, and the expiry is set whenever the key
    # has none, not only on the first hit. A process that died between INCR and
    # EXPIRE used to leave a counter that never expired, locking the IP or
    # account out for good; the next call now repairs it. TTL is used rather
    # than EXPIRE NX so this works on any Redis server version.
    async with r.pipeline(transaction=True) as pipe:
        current, ttl = await pipe.incr(redis_key).ttl(redis_key).execute()
    if ttl == -1:
        await r.expire(redis_key, window_seconds)

    if current > max_attempts:
        raise RateLimitError()


async def check_login_rate_limit(request: Request, email: str) -> None:
    """Apply rate limits on login: per-IP and per-account."""
    client_ip = request.client.host if request.client else "unknown"

    # 10 attempts per IP per minute
    await check_rate_limit(f"ip:{client_ip}", max_attempts=10, window_seconds=60)

    # 5 attempts per email per minute
    await check_rate_limit(f"email:{email}", max_attempts=5, window_seconds=60)
