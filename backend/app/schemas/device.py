from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Platform = Literal["macos", "windows", "browser"]


class DeviceRegisterIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: Platform
    app_version: str | None = Field(None, max_length=50)
    fingerprint: str | None = Field(None, max_length=100)
    user_external_id: str | None = Field(None, max_length=320)


class DeviceRegisterOut(BaseModel):
    device_id: str
    device_token: str  # returned once; client stores in Keychain


class DeviceHeartbeatOut(BaseModel):
    ok: bool = True
