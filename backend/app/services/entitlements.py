"""Plan → capability gate. Pure functions, no I/O, no DB writes."""

from __future__ import annotations

import enum
from datetime import datetime

from app.models.tenant import Plan, Tenant


class Capability(str, enum.Enum):
    INVITE_USERS = "invite_users"
    LLM_COST_METER = "llm_cost_meter"
    DEVELOPER_API = "developer_api"


# Team is the only paid tier wired this iteration; trialing tenants are
# Plan.FREE until checkout.session.completed flips them to Plan.TEAM, so
# trial entitlements are governed by hard_locked(), not by FREE caps.
_TEAM_CAPS: set[Capability] = {
    Capability.INVITE_USERS,
    Capability.LLM_COST_METER,
    Capability.DEVELOPER_API,
}
PLAN_ENTITLEMENTS: dict[Plan, set[Capability]] = {
    Plan.FREE: set(),
    Plan.PRO: {Capability.INVITE_USERS, Capability.LLM_COST_METER},
    Plan.TEAM: _TEAM_CAPS,
    Plan.ENTERPRISE: set(_TEAM_CAPS),
}


def can(tenant: Tenant, cap: Capability) -> bool:
    """Single-source-of-truth capability gate. Default-deny."""
    return cap in PLAN_ENTITLEMENTS.get(tenant.plan, set())


def hard_locked(tenant: Tenant, *, now: datetime) -> bool:
    """True iff the trial expired without a Stripe subscription attached.

    `now` MUST be the DB server's now() at the call site (never
    datetime.now() in app code) to avoid clock skew. Passed in so this
    stays pure/testable.
    """
    return (
        tenant.trial_ends_at is not None
        and tenant.trial_ends_at < now
        and tenant.stripe_subscription_id is None
    )
