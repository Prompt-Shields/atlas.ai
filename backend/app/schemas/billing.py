"""Pydantic schemas for billing endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool


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


class GuidedStepsResponse(BaseModel):
    """The Work client's hosted guided steps this month (promptly-guide #62).

    Step counts only, never money: the price of a step above the pool lives in
    Stripe, not in Atlas. Nothing here says who used a step (E4).
    """

    period: str  # the UTC calendar month, "YYYY-MM"
    used: int  # hosted steps served this month
    pool: int  # steps the Work client includes this month
    remaining: int  # steps left in the pool, never below 0
    overage: int  # steps above the pool; 0 for an organisation with its own endpoint
    hard_cap: bool  # hosted steps stop at the pool until the month turns
    own_endpoint: bool  # Guide answers from the organisation's own model endpoint
    work_client: bool  # the tenant has a Work client


class GuidedStepHardCapUpdate(BaseModel):
    """Turn the hard cap on or off. Off by default (step-metering.md decision 4)."""

    model_config = ConfigDict(extra="forbid")

    hard_cap: StrictBool
