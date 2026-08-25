"""Invite Pydantic schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.auth import _validate_password_strength


class InviteCreate(BaseModel):
    email: EmailStr
    tenant_id: str
    role: str = Field(
        default="TENANT_ADMIN",
        pattern=r"^(TENANT_ADMIN|ORG_ADMIN|ANALYST|VIEWER)$",
    )


class InviteConfirm(BaseModel):
    token: str = Field(..., min_length=1, max_length=256)
    password: str = Field(..., min_length=12, max_length=128)
    full_name: str = Field(..., min_length=1, max_length=255)

    @field_validator("password")
    @classmethod
    def strong_password(cls, v: str) -> str:
        return _validate_password_strength(v)


class InviteResponse(BaseModel):
    id: str
    email: str
    tenant_id: str
    role: str
    is_used: bool
    is_revoked: bool
    expires_at: datetime
    created_at: datetime

    class Config:
        from_attributes = True


class InviteListResponse(BaseModel):
    invites: list[InviteResponse]
    total: int
