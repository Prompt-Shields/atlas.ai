"""Unit tests for JWT creation and decoding."""

from __future__ import annotations

import time
import uuid

import pytest
from jose import JWTError

from app.auth.jwt import create_access_token, create_refresh_token, decode_token

pytestmark = pytest.mark.unit

TEST_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000099")


class TestJWT:
    def test_create_and_decode_access_token(self) -> None:
        token = create_access_token(
            user_id=TEST_USER_ID,
            email="u@t.com",
            roles=["ANALYST"],
            tenant_id=TEST_USER_ID,
            org_id=TEST_USER_ID,
        )
        payload = decode_token(token)
        assert payload["sub"] == str(TEST_USER_ID)
        assert payload["email"] == "u@t.com"
        assert "ANALYST" in payload["roles"]
        assert "exp" in payload
        assert payload["type"] == "access"

    def test_create_refresh_token(self) -> None:
        token = create_refresh_token(user_id=TEST_USER_ID)
        payload = decode_token(token)
        assert payload["sub"] == str(TEST_USER_ID)
        assert payload["type"] == "refresh"

    def test_decode_invalid_token(self) -> None:
        with pytest.raises(JWTError):
            decode_token("not.a.valid.token")

    def test_access_token_has_expiry(self) -> None:
        token = create_access_token(
            user_id=TEST_USER_ID,
            email="u@t.com",
            roles=["VIEWER"],
        )
        payload = decode_token(token)
        assert payload["exp"] > time.time()

    def test_access_and_refresh_tokens_are_different(self) -> None:
        access = create_access_token(
            user_id=TEST_USER_ID,
            email="u@t.com",
            roles=["VIEWER"],
        )
        refresh = create_refresh_token(user_id=TEST_USER_ID)
        assert access != refresh

        access_payload = decode_token(access)
        refresh_payload = decode_token(refresh)
        assert access_payload["type"] == "access"
        assert refresh_payload["type"] == "refresh"
