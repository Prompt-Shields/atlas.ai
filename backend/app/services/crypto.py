"""Symmetric encryption helpers — used for Slack workspace tokens.

Backed by `cryptography.Fernet` (AES-128-CBC + HMAC-SHA256 + URL-safe
base64). Key lives in env as `SLACK_TOKEN_ENCRYPTION_KEY` — a 32-byte
URL-safe base64 string. In Azure this is wired through Container
Apps → Key Vault reference; locally via .env.

Rotation: write a new key, deploy, then re-encrypt all existing
ciphertexts. The `EncryptionService` supports a list of keys via
`MultiFernet` so old ciphertexts decrypt until rotation completes.

The atlas backend has no existing Key Vault adapter, so this is the
single dependency-light shim. If a Key Vault adapter lands later, the
swap is local to `_load_keys()` — call sites don't change.
"""

from __future__ import annotations

import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class TokenEncryptionError(Exception):
    """Raised when ciphertext is malformed or no key decrypts it."""


@lru_cache(maxsize=1)
def _service() -> MultiFernet:
    """Construct the MultiFernet from env-supplied keys.

    Reads `SLACK_TOKEN_ENCRYPTION_KEY` (required) and optionally
    `SLACK_TOKEN_ENCRYPTION_KEY_OLD` for rotation overlap. Both must
    be valid 32-byte URL-safe base64. Lifetimed-cached per process —
    rotation in production requires a deploy.
    """
    keys = _load_keys()
    if not keys:
        raise TokenEncryptionError(
            "SLACK_TOKEN_ENCRYPTION_KEY not set — required for Slack workspace "
            "token encryption. Generate with `python -c "
            "'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`"
        )
    fernets = [Fernet(k.encode() if isinstance(k, str) else k) for k in keys]
    return MultiFernet(fernets)


def _load_keys() -> list[str]:
    """Return non-empty key list in newest→oldest order."""
    keys: list[str] = []
    primary = os.environ.get("SLACK_TOKEN_ENCRYPTION_KEY", "").strip()
    if primary:
        keys.append(primary)
    legacy = os.environ.get("SLACK_TOKEN_ENCRYPTION_KEY_OLD", "").strip()
    if legacy:
        keys.append(legacy)
    return keys


def encrypt_token(plaintext: str) -> str:
    """Encrypt a Slack workspace token. Returns a URL-safe base64 string."""
    if not isinstance(plaintext, str):
        raise TokenEncryptionError("plaintext must be a string")
    if not plaintext:
        raise TokenEncryptionError("refusing to encrypt empty plaintext")
    return _service().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_token(ciphertext: str) -> str:
    """Decrypt — raises TokenEncryptionError if no current key matches."""
    if not isinstance(ciphertext, str) or not ciphertext:
        raise TokenEncryptionError("ciphertext must be a non-empty string")
    try:
        plain = _service().decrypt(ciphertext.encode("ascii"))
    except InvalidToken as exc:
        raise TokenEncryptionError(
            "ciphertext does not decrypt with any configured key — rotate or "
            "verify SLACK_TOKEN_ENCRYPTION_KEY[_OLD]"
        ) from exc
    return plain.decode("utf-8")


def rotate_ciphertext(ciphertext: str) -> str:
    """Re-encrypt a ciphertext with the newest key.

    Used in batch during a key rotation: read every encrypted column,
    call this, write back. Idempotent if already at the newest key.
    """
    plain = decrypt_token(ciphertext)
    return encrypt_token(plain)


def reset_cache_for_tests() -> None:
    """Test hook — clears the lru_cache so a fresh env can be reloaded."""
    _service.cache_clear()
