"""User Pydantic schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.auth import _validate_password_strength


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=12, max_length=128)
    full_name: str = Field(..., min_length=1, max_length=255)
    role: str = Field(default="VIEWER", pattern=r"^(TENANT_ADMIN|ORG_ADMIN|ANALYST|VIEWER)$")
    org_id: str | None = None

    @field_validator("password")
    @classmethod
    def strong_password(cls, v: str) -> str:
        return _validate_password_strength(v)


class UserUpdate(BaseModel):
    full_name: str | None = Field(None, min_length=1, max_length=255)
    is_active: bool | None = None
    role: str | None = Field(None, pattern=r"^(TENANT_ADMIN|ORG_ADMIN|ANALYST|VIEWER)$")


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    is_active: bool
    is_email_verified: bool
    tenant_id: str | None
    org_id: str | None
    roles: list[str]
    # "sso" if linked to an Entra identity (entra_object_id set), else "local".
    auth_source: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UserListResponse(BaseModel):
    users: list[UserResponse]
    total: int
    page: int
    page_size: int
