"""API key generation and validation."""

from __future__ import annotations

import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import APIKey

PREFIX = "aigrc_"
KEY_LENGTH = 32


def generate_api_key() -> tuple[str, str, str]:
    """Generate a new API key. Returns (full_key, key_prefix, hashed_key)."""
    raw = secrets.token_urlsafe(KEY_LENGTH)
    full_key = f"{PREFIX}{raw}"
    key_prefix = full_key[:8]
    hashed_key = hashlib.sha256(full_key.encode()).hexdigest()
    return full_key, key_prefix, hashed_key


def hash_api_key(key: str) -> str:
    """Hash an API key for storage/comparison."""
    return hashlib.sha256(key.encode()).hexdigest()


async def validate_api_key(
    db: AsyncSession,
    key: str,
) -> APIKey | None:
    """Validate an API key and return the APIKey record if valid."""
    hashed = hash_api_key(key)
    result = await db.execute(
        select(APIKey).where(
            APIKey.hashed_key == hashed,
            APIKey.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none()
