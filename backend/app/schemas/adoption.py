"""Pydantic schemas for the Adoption usage-quality scorecard (issue #249, M6).

Wire format for `app.services.adoption_service.build_scorecard()` — a
transcript -> scorecard pipeline currently backed by a seed session set
(see that module for why), stateless like the PII Shield router.
"""

from __future__ import annotations

from pydantic import BaseModel


class QualityDimension(BaseModel):
    """One of the four usage-quality dimensions: outcome, leverage, trajectory, diffusion."""

    key: str
    label: str
    score: int
    trend: float
    narrative: str


class UsageQualityHeadline(BaseModel):
    """ROI + upskilling summary shown above the four dimension cards."""

    roi_multiplier: float
    hours_saved_per_week: float
    upskilling_score: int
    window_days: int


class UsageQualityScorecardResponse(BaseModel):
    headline: UsageQualityHeadline
    dimensions: list[QualityDimension]
