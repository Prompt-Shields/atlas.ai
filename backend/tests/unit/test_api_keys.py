"""Unit tests for API key generation and hashing."""

from __future__ import annotations

import pytest

from app.auth.api_keys import generate_api_key, hash_api_key

pytestmark = pytest.mark.unit


class TestAPIKeys:
    def test_generate_api_key_format(self) -> None:
        full_key, key_prefix, hashed_key = generate_api_key()
        assert full_key.startswith("aigrc_")
        assert len(full_key) > 10

    def test_key_prefix_matches(self) -> None:
        full_key, key_prefix, hashed_key = generate_api_key()
        assert full_key.startswith(key_prefix)

    def test_hash_api_key(self) -> None:
        full_key, key_prefix, hashed_key = generate_api_key()
        recomputed = hash_api_key(full_key)
        assert recomputed == hashed_key
        assert len(hashed_key) == 64  # SHA-256 hex digest

    def test_hash_api_key_consistent(self) -> None:
        full_key, _, _ = generate_api_key()
        h1 = hash_api_key(full_key)
        h2 = hash_api_key(full_key)
        assert h1 == h2

    def test_different_keys_different_hashes(self) -> None:
        k1, _, h1 = generate_api_key()
        k2, _, h2 = generate_api_key()
        assert k1 != k2
        assert h1 != h2

    def test_keys_are_unique(self) -> None:
        keys = {generate_api_key()[0] for _ in range(100)}
        assert len(keys) == 100
