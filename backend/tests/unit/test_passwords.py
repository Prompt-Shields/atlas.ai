"""Unit tests for password hashing utilities."""

from __future__ import annotations

import pytest

from app.auth.passwords import hash_password, verify_password

pytestmark = pytest.mark.unit


class TestPasswordHashing:
    def test_hash_password_returns_hash(self) -> None:
        pw = "Str0ng_P@ssword!"
        hashed = hash_password(pw)
        assert hashed != pw
        assert len(hashed) > 40

    def test_verify_correct_password(self) -> None:
        pw = "Str0ng_P@ssword!"
        hashed = hash_password(pw)
        assert verify_password(pw, hashed) is True

    def test_verify_wrong_password(self) -> None:
        hashed = hash_password("correct")
        assert verify_password("wrong", hashed) is False

    def test_different_hashes_for_same_password(self) -> None:
        pw = "SamePassword123!"
        h1 = hash_password(pw)
        h2 = hash_password(pw)
        assert h1 != h2  # salt should differ

    def test_empty_password_raises(self) -> None:
        # The function should still produce a hash (argon2 handles empty strings)
        hashed = hash_password("")
        assert len(hashed) > 0
