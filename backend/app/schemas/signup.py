"""Signup request/response Pydantic schemas."""

from __future__ import annotations

import re

from pydantic import BaseModel, EmailStr, Field, field_validator

_SLUG_RE = re.compile(r"^[a-z0-9-]{3,100}$")


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=12, max_length=128)
    tenant_name: str = Field(..., min_length=1, max_length=255)
    tenant_slug: str = Field(..., min_length=3, max_length=100)
    full_name: str = Field(default="", max_length=255)

    @field_validator("tenant_slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not _SLUG_RE.match(v):
            raise ValueError("slug must be 3-100 chars of [a-z0-9-]")
        return v


class SignupResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    tenant_id: str
