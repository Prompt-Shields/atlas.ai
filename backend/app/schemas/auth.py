"""Auth Pydantic schemas."""

from __future__ import annotations

import re

from pydantic import BaseModel, EmailStr, Field, field_validator


def _validate_password_strength(password: str) -> str:
    """Enforce password complexity: min 1 uppercase, 1 lowercase, 1 digit, 1 special."""
    if not re.search(r"[A-Z]", password):
        raise ValueError("Password must contain at least one uppercase letter")
    if not re.search(r"[a-z]", password):
        raise ValueError("Password must contain at least one lowercase letter")
    if not re.search(r"\d", password):
        raise ValueError("Password must contain at least one digit")
    if not re.search(r"[^A-Za-z0-9]", password):
        raise ValueError("Password must contain at least one special character")
    return password


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    api_key: str | None = Field(
        None,
        description="Required for sensitive routes. Per-user or per-tenant API key.",
    )


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class SSOExchangeRequest(BaseModel):
    """SPA exchanges the one-time SSO handoff code for a token pair (PRO-55)."""

    code: str


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(..., min_length=8)
    new_password: str = Field(..., min_length=12, max_length=128)

    @field_validator("new_password")
    @classmethod
    def strong_password(cls, v: str) -> str:
        return _validate_password_strength(v)


class APIKeyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None


class APIKeyCreateResponse(BaseModel):
    id: str
    key: str  # Only returned once at creation
    key_prefix: str
    name: str
    description: str | None = None

    class Config:
        json_schema_extra = {
            "example": {
                "id": "uuid",
                "key": "aigrc_xxxxxxxxxxxx",
                "key_prefix": "aigrc_xx",
                "name": "My API Key",
            }
        }


class APIKeyResponse(BaseModel):
    id: str
    key_prefix: str
    name: str
    description: str | None = None
    is_active: bool
    created_at: str
