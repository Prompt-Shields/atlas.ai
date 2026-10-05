from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AdoptionKind = Literal["topic", "app", "completion"]


class GuideConnectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    firebase_project_id: str = Field(..., min_length=1, max_length=128)
    # Identity Platform tenant id; "" when the project does not use tenants.
    firebase_tenant_id: str = Field("", max_length=128)
    # What the organisation offers to count. Each person still decides (#38).
    offered_kinds: list[AdoptionKind] = Field(default_factory=list)


class GuideConnectionOut(BaseModel):
    firebase_project_id: str
    firebase_tenant_id: str
    offered_kinds: list[AdoptionKind]


class GuideOfferOut(BaseModel):
    """What Guide shows the person before they decide whether to be counted."""

    offered_kinds: list[AdoptionKind]
    minimum_group_size: int


class GuideReachedIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(..., max_length=20)
    id: str = Field(..., max_length=120)


class GuidePeriodIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    year: int
    month: int


class GuideContributionIn(BaseModel):
    """Guide's `AdoptionContribution`, exactly: a team, a month, the categories
    reached. Anything else is refused (`extra="forbid"`)."""

    model_config = ConfigDict(extra="forbid")

    team: str = Field(..., max_length=200)
    period: GuidePeriodIn
    reached: list[GuideReachedIn] = Field(default_factory=list, max_length=200)


class GuideContributionOut(BaseModel):
    counted: bool


class GuideFigureOut(BaseModel):
    team: str
    category_kind: str
    category_id: str
    band: str
    band_lower: int


class GuideAdoptionReportOut(BaseModel):
    period: str
    figures: list[GuideFigureOut]
    teams_too_small: list[str]
    suppressed_categories: dict[str, int]
    minimum_group_size: int
    # Said beside every figure: only people who opted in are counted (#38), so a
    # band is a share of them, not of the whole team.
    note: str
