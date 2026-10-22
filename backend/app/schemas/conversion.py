"""Schemas for the free-to-paid conversion report (promptly-guide#43).

Every field is about a company. See app.services.conversion_report.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ConversionCompany(BaseModel):
    tenant_id: uuid.UUID
    name: str
    stripe_customer_id: str | None
    cohort: str | None
    work_client_started_at: datetime | None
    guide_connected_at: datetime | None
    work_client_live: bool
    paid_module_now: bool
    suite_now: bool
    seats_now: int | None
    lost: bool
    free_client_status: Literal["active", "grace_ending", "endpoint_required"] | None
    window_open: bool


class ConversionCohort(BaseModel):
    cohort: str | None
    started: int
    connected: int
    paying_now_any_module: int
    paying_now_suite: int
    lost: int
    endpoint_required: int
    paying_now_rate_any_module: float | None
    paying_now_rate_suite: float | None
    median_seats_paying: float | None
    window: Literal["so far", "closed"]


class ConversionReportResponse(BaseModel):
    generated_at: datetime
    # Below this many companies a cohort's rates are null: read the counts.
    min_companies_for_rate: int
    cohorts: list[ConversionCohort]
    companies: list[ConversionCompany]
    # Stages and figures the note defines that need Stripe, with the reason.
    not_from_atlas: dict[str, str]
