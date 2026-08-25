"""Redis-backed refresh token store for revocation and replay detection.

Each refresh token's JTI is stored in Redis with a TTL matching the token
lifetime.  On rotation the old JTI is revoked; if a revoked JTI is reused
the entire token family (user) is invalidated.
"""

from __future__ import annotations

import json
import uuid

import redis.asyncio as redis

from app.config import get_settings

_redis_client: redis.Redis | None = None

_FAMILY_PREFIX = "rt_family:"
_JTI_PREFIX = "rt_jti:"
_SSO_CODE_PREFIX = "sso_code:"


def _get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


async def close_token_store() -> None:
    global _redis_client
    if _redis_client:
        await _redis_client.aclose()
        _redis_client = None


async def store_refresh_token(user_id: uuid.UUID, jti: str, ttl_seconds: int) -> None:
    """Store a refresh token JTI as valid."""
    r = _get_redis()
    jti_key = f"{_JTI_PREFIX}{jti}"
    family_key = f"{_FAMILY_PREFIX}{user_id}"

    pipe = r.pipeline()
    pipe.set(jti_key, str(user_id), ex=ttl_seconds)
    pipe.sadd(family_key, jti)
    pipe.expire(family_key, ttl_seconds)
    await pipe.execute()


async def consume_refresh_token(jti: str, user_id: uuid.UUID) -> bool:
    """Consume (use + revoke) a refresh token.

    Returns True if the token was valid and is now consumed.
    Returns False if the token was already revoked (replay attack)
    — in that case, the entire user's token family is invalidated.
    """
    r = _get_redis()
    jti_key = f"{_JTI_PREFIX}{jti}"

    value = await r.get(jti_key)
    if value is None:
        # Token not found or already consumed — possible replay attack.
        # Revoke ALL refresh tokens for this user as a precaution.
        await revoke_all_for_user(user_id)
        return False

    # Revoke this specific token
    await r.delete(jti_key)
    return True


async def revoke_all_for_user(user_id: uuid.UUID) -> int:
    """Revoke all refresh tokens for a user (logout-everywhere)."""
    r = _get_redis()
    family_key = f"{_FAMILY_PREFIX}{user_id}"

    jtis = await r.smembers(family_key)
    if not jtis:
        return 0

    pipe = r.pipeline()
    for jti in jtis:
        pipe.delete(f"{_JTI_PREFIX}{jti}")
    pipe.delete(family_key)
    await pipe.execute()
    return len(jtis)


# ─── One-time SSO handoff codes (PRO-55) ─────────────────────────────
# After the SSO callback validates the user it mints a short-lived,
# single-use code and redirects the browser to the SPA with it (no tokens
# in the URL). The SPA exchanges the code for the real token pair.


async def store_sso_code(code: str, tokens: dict, ttl_seconds: int = 120) -> None:
    """Stash the issued token pair under a one-time code (short TTL)."""
    r = _get_redis()
    await r.set(f"{_SSO_CODE_PREFIX}{code}", json.dumps(tokens), ex=ttl_seconds)


async def consume_sso_code(code: str) -> dict | None:
    """Atomically fetch + delete the token pair for a code (single-use).

    Returns None if the code is unknown, expired, or already consumed.
    """
    r = _get_redis()
    raw = await r.getdel(f"{_SSO_CODE_PREFIX}{code}")
    if raw is None:
        return None
    return json.loads(raw)
