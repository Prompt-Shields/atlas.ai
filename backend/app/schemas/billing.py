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
    # The free Work client's 90-day rule (promptly-guide#52). Status is None
    # when the tenant has not started the Work client.
    free_client_status: Literal["active", "grace_ending", "endpoint_required"] | None = None
    work_client_started_at: datetime | None = None
    work_client_days: int | None = None
    free_client_grace_ends_at: datetime | None = None
    paid_module_active: bool = False
    own_endpoint_declared: bool = False


class WorkClientUpdate(BaseModel):
    """Admin changes to the Work client's 90-day inputs. Omitted fields are left alone."""

    # True records day 0 if it is not set yet. Day 0 is never moved or
    # cleared here, so the 90 days cannot be restarted from the admin page.
    started: Literal[True] | None = None
    own_endpoint_declared: bool | None = None
