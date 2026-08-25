"""Adoption router — `/api/v1/adoption` (issue #249, M6).

  GET  /adoption/usage-quality   Any   ROI/upskilling headline + 4 quality dimensions

Stateless, same shape as `routers/pii.py`: nothing here reads or writes the
database yet. `adoption_service.build_scorecard()` runs the aggregation
over a seed session set standing in for a real transcript pipeline — see
that module's docstring for the migration path once telemetry exists.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.auth.dependencies import AuthUser
from app.schemas.adoption import (
    QualityDimension,
    UsageQualityHeadline,
    UsageQualityScorecardResponse,
)
from app.services import adoption_service

router = APIRouter(prefix="/adoption", tags=["Adoption"])


@router.get("/usage-quality", response_model=UsageQualityScorecardResponse)
async def usage_quality(user: AuthUser) -> UsageQualityScorecardResponse:
    scorecard = adoption_service.build_scorecard()
    return UsageQualityScorecardResponse(
        headline=UsageQualityHeadline(
            roi_multiplier=scorecard.headline.roi_multiplier,
            hours_saved_per_week=scorecard.headline.hours_saved_per_week,
            upskilling_score=scorecard.headline.upskilling_score,
            window_days=scorecard.headline.window_days,
        ),
        dimensions=[
            QualityDimension(
                key=d.key,
                label=d.label,
                score=d.score,
                trend=d.trend,
                narrative=d.narrative,
            )
            for d in scorecard.dimensions
        ],
    )
