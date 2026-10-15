"""Plan → capability gate, and the free Work client's 90-day rule.

Pure functions, no I/O, no DB writes.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.models.tenant import Plan, SubscriptionStatus, Tenant


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


# ---------------------------------------------------------------------------
# Free Work client: the 90-day rule (promptly-guide#52)
# ---------------------------------------------------------------------------
# "After 90 days without a paid module, the customer must connect its own
# model endpoint to keep the client." This works the status out; nothing here
# enforces it. Guide has no Promptly-hosted model yet, so there is nothing to
# withhold. Guide will read the status and show admins the requirement.

FREE_CLIENT_GRACE_DAYS = 90
# How long before day 90 admins are warned. A product choice, not the plan's.
GRACE_ENDING_WARNING_DAYS = 14


class FreeClientStatus(str, enum.Enum):
    ACTIVE = "active"  # inside the 90 days, or exempt (paid module, own endpoint)
    GRACE_ENDING = "grace_ending"  # the last GRACE_ENDING_WARNING_DAYS of the 90
    ENDPOINT_REQUIRED = "endpoint_required"  # day 90 passed, no module, no endpoint


@dataclass(frozen=True)
class FreeClientGrace:
    """The rule's inputs and verdict for one tenant. `status` None = no Work client."""

    status: FreeClientStatus | None
    started_at: datetime | None
    days_since_start: int | None
    grace_ends_at: datetime | None
    paid_module_active: bool
    own_endpoint_declared: bool


def paid_module_active(tenant: Tenant) -> bool:
    """True iff the tenant pays for a module.

    Atlas.AI is the only module Atlas bills today, as a paid plan. A canceled
    subscription leaves the plan in place (the webhook only sets the status),
    so the status is checked too. A paid plan with no status is one set by
    hand, as a quote-and-invoice enterprise deal would be, and counts as paid.
    The Atlas trial does not: it is Plan.FREE until checkout.
    """
    return (
        tenant.plan is not Plan.FREE
        and tenant.subscription_status is not SubscriptionStatus.CANCELED
    )


def free_client_grace(tenant: Tenant, *, now: datetime) -> FreeClientGrace:
    """Where the tenant stands on the 90-day rule.

    `now` MUST be the DB server's now(), as for hard_locked().
    """
    started = tenant.work_client_started_at
    paid = paid_module_active(tenant)
    own = tenant.own_endpoint_declared_at is not None
    if started is None:
        return FreeClientGrace(None, None, None, None, paid, own)

    ends = started + timedelta(days=FREE_CLIENT_GRACE_DAYS)
    days = max((now - started).days, 0)
    if paid or own:
        status = FreeClientStatus.ACTIVE
    elif now >= ends:
        status = FreeClientStatus.ENDPOINT_REQUIRED
    elif now >= ends - timedelta(days=GRACE_ENDING_WARNING_DAYS):
        status = FreeClientStatus.GRACE_ENDING
    else:
        status = FreeClientStatus.ACTIVE
    return FreeClientGrace(status, started, days, ends, paid, own)
