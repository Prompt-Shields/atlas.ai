"""Pydantic schemas for billing endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CheckoutSessionRequest(BaseModel):
    interval: Literal["monthly", "annual"]


class CheckoutSessionResponse(BaseModel):
    url: str


class PortalSessionResponse(BaseModel):
    url: str


class EntitlementsResponse(BaseModel):
    plan: str
    subscription_status: str | None
    trial_ends_at: datetime | None
    seats_used: int
    seats_billed: int
    capabilities: list[str]
    hard_locked: bool
    # Promptly modules switched on by the subscription's items: any of
    # "work_client", "atlas", "learn", "adopt". Added after the fields above,
    # with a default, so existing clients are unaffected.
    modules: list[str] = Field(default_factory=list)
