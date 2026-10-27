"""Guided-step caps (promptly-guide#62). No I/O beyond reading the settings.

A guided step is one screen read with an answer; a five-step walkthrough is
five. Stripe meters but does not block, so the caps live here.

**The figures are not in this repository.** How many steps each plan includes
is a commercial term, and this repository is public (CONTRIBUTING.md), so the
allowances come from configuration: `GUIDED_STEPS_FREE_MONTHLY`,
`GUIDED_STEPS_PLUS_MONTHLY` and `GUIDED_STEPS_POOL_PER_USER` (README,
"Guided-step allowances"). The price of a step above the pool lives in its
Stripe Price, never in Atlas.

**An allowance that is not configured serves nothing.** Without it Atlas
cannot tell a step inside the pool from one above it, so a hosted step is
refused, rather than served and then billed, or not billed, by guesswork.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from app.config import get_settings


class GuideTier(str, enum.Enum):
    FREE = "free"
    PLUS = "plus"
    ENTERPRISE = "enterprise"


class Decision(str, enum.Enum):
    ALLOW = "allow"
    SLOW = "slow"  # Plus over its cap: served, but slowed
    BLOCK = "block"  # Free over its cap, or a tier whose allowance is not configured


@dataclass(frozen=True)
class StepCaps:
    """The plans' monthly allowances, as configured. None: not configured."""

    free_monthly: int | None = None
    plus_monthly: int | None = None
    pool_per_user: int | None = None  # the Work client's steps per provisioned user


def step_caps() -> StepCaps:
    """The allowances, from the settings."""
    s = get_settings()
    return StepCaps(
        free_monthly=s.guided_steps_free_monthly,
        plus_monthly=s.guided_steps_plus_monthly,
        pool_per_user=s.guided_steps_pool_per_user,
    )


@dataclass(frozen=True)
class StepVerdict:
    decision: Decision
    meter_step: bool  # post this step to the Stripe meter


def enterprise_pool(seats: int, caps: StepCaps | None = None) -> int | None:
    """The Work client's pool for `seats` provisioned users; None if not configured."""
    per_user = (caps or step_caps()).pool_per_user
    if per_user is None:
        return None
    return max(seats, 0) * per_user


def decide(
    tier: GuideTier,
    *,
    used: int,
    seats: int = 0,
    own_endpoint: bool = False,
    caps: StepCaps | None = None,
) -> StepVerdict:
    """Verdict for the next step, given `used` steps already counted this month.

    Enterprise `used` and `seats` are company-wide (the pool is shared). Only
    steps above the pool are metered, and none for own-endpoint customers. A
    tier whose allowance is not configured is blocked and never metered.
    """
    caps = caps or step_caps()
    if tier is GuideTier.FREE:
        if caps.free_monthly is None or used >= caps.free_monthly:
            return StepVerdict(Decision.BLOCK, False)
        return StepVerdict(Decision.ALLOW, False)
    if tier is GuideTier.PLUS:
        if caps.plus_monthly is None:
            return StepVerdict(Decision.BLOCK, False)
        over = used >= caps.plus_monthly
        return StepVerdict(Decision.SLOW if over else Decision.ALLOW, False)
    if own_endpoint:
        return StepVerdict(Decision.ALLOW, False)
    pool = enterprise_pool(seats, caps)
    if pool is None:
        return StepVerdict(Decision.BLOCK, False)
    return StepVerdict(Decision.ALLOW, used >= pool)
