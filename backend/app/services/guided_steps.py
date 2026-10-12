"""Guided-step caps and overage (promptly-guide#62). Pure functions, no I/O.

A guided step is one screen read with an answer; a five-step walkthrough is
five. Stripe meters but does not block, so the caps live here.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

FREE_MONTHLY_CAP = 25
PLUS_MONTHLY_CAP = 400
ENTERPRISE_STEPS_PER_USER = 50
OVERAGE_EUR_PER_STEP = 0.04


class GuideTier(str, enum.Enum):
    FREE = "free"
    PLUS = "plus"
    ENTERPRISE = "enterprise"


class Decision(str, enum.Enum):
    ALLOW = "allow"
    SLOW = "slow"  # Plus over its cap: served, but slowed
    BLOCK = "block"  # Free over its cap


@dataclass(frozen=True)
class StepVerdict:
    decision: Decision
    meter_step: bool  # post this step to the Stripe meter


def enterprise_pool(seats: int) -> int:
    return max(seats, 0) * ENTERPRISE_STEPS_PER_USER


def decide(
    tier: GuideTier,
    *,
    used: int,
    seats: int = 0,
    own_endpoint: bool = False,
) -> StepVerdict:
    """Verdict for the next step, given `used` steps already counted this month.

    Enterprise `used` and `seats` are company-wide (the pool is shared). Only
    steps above the pool are metered, and none for own-endpoint customers.
    """
    if tier is GuideTier.FREE:
        over = used >= FREE_MONTHLY_CAP
        return StepVerdict(Decision.BLOCK if over else Decision.ALLOW, False)
    if tier is GuideTier.PLUS:
        over = used >= PLUS_MONTHLY_CAP
        return StepVerdict(Decision.SLOW if over else Decision.ALLOW, False)
    over_pool = used >= enterprise_pool(seats)
    return StepVerdict(Decision.ALLOW, over_pool and not own_endpoint)


def overage_eur(used: int, seats: int) -> float:
    """Month-end overage charge, in arrears."""
    return round(max(used - enterprise_pool(seats), 0) * OVERAGE_EUR_PER_STEP, 2)
