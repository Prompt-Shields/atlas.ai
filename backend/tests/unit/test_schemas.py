"""Unit tests for Pydantic schema validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.auth import LoginRequest
from app.schemas.blob import BlobIngestRequest
from app.schemas.tenant import TenantCreate
from app.schemas.user import UserCreate

pytestmark = pytest.mark.unit


class TestLoginRequest:
    def test_valid_login(self) -> None:
        req = LoginRequest(email="user@example.com", password="StrongP@ss1")
        assert req.email == "user@example.com"

    def test_invalid_email(self) -> None:
        with pytest.raises(ValidationError):
            LoginRequest(email="not-an-email", password="StrongP@ss1")

    def test_empty_password(self) -> None:
        with pytest.raises(ValidationError):
            LoginRequest(email="user@example.com", password="")


class TestTenantCreate:
    def test_valid_tenant(self) -> None:
        t = TenantCreate(name="Acme Corp", slug="acme-corp")
        assert t.slug == "acme-corp"

    def test_slug_validation(self) -> None:
        # Slugs must be lowercase alphanumeric with hyphens
        with pytest.raises(ValidationError):
            TenantCreate(name="Bad", slug="INVALID SLUG!")


class TestUserCreate:
    def test_valid_user(self) -> None:
        u = UserCreate(
            email="new@example.com",
            password="SecureP@ss123!",
            full_name="Jane Doe",
            role="ANALYST",
        )
        assert u.role == "ANALYST"

    def test_invalid_role(self) -> None:
        with pytest.raises(ValidationError):
            UserCreate(
                email="new@example.com",
                password="SecureP@ss123!",
                full_name="Jane Doe",
                role="GODMODE",
            )


class TestBlobIngestRequest:
    def test_valid_blob(self) -> None:
        b = BlobIngestRequest(
            content="Some compliance text to ingest",
            source_type="manual",
        )
        assert b.source_type == "manual"

    def test_empty_content(self) -> None:
        with pytest.raises(ValidationError):
            BlobIngestRequest(content="", source_type="manual")
