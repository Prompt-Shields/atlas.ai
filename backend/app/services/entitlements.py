"""Plan → capability gate, module entitlements, and the free Work client's
90-day rule.

Pure functions, no I/O, no DB writes.
"""

from __future__ import annotations

import enum
import math
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
# Modules
# ---------------------------------------------------------------------------


class Module(str, enum.Enum):
    WORK_CLIENT = "work_client"
    ATLAS = "atlas"
    LEARN = "learn"
    ADOPT = "adopt"


# Stripe price lookup_key → modules that price switches on. Keys are the ones
# configured in Stripe (Promptly business plan, "Stripe setup"); mapping by
# lookup_key rather than price id keeps test and live mode the same and lets
# a price be replaced without a deploy. The platform fee and step overage are
# billed but grant nothing on their own. A key not listed here grants nothing.
LOOKUP_KEY_MODULES: dict[str, frozenset[Module]] = {
    "promptly_work_client": frozenset({Module.WORK_CLIENT}),
    "promptly_atlas_annual": frozenset({Module.ATLAS}),
    "promptly_learn_annual": frozenset({Module.LEARN}),
    "promptly_adopt_annual": frozenset({Module.ADOPT}),
    "promptly_suite_annual": frozenset({Module.ATLAS, Module.LEARN, Module.ADOPT}),
    "promptly_platform_fee_annual": frozenset(),
    "promptly_step_overage": frozenset(),
}

# The Work client is free, so an unpaid invoice for modules does not take it
# away; only a subscription that has ended does.
_FREE_MODULES: frozenset[Module] = frozenset({Module.WORK_CLIENT})

# Stripe subscription statuses, as stored per subscription.
_GRANTING_STATUSES = {"active", "trialing"}
_UNPAID_STATUSES = {"past_due", "unpaid"}


def subscription_items_from_stripe(items: list[dict]) -> dict[str, int | None]:
    """Reduce Stripe ``subscription.items.data`` to {lookup_key: quantity}.

    Items whose price has no lookup_key (for example the Atlas Team price) are
    dropped: they map to no module. Metered items carry no quantity, hence None.
    """
    out: dict[str, int | None] = {}
    for item in items:
        key = (item.get("price") or {}).get("lookup_key")
        if key:
            out[key] = item.get("quantity")
    return out


def _granted_by(items: dict | None, status: str | None) -> set[Module]:
    granted: set[Module] = set()
    for key in items or {}:
        granted |= LOOKUP_KEY_MODULES.get(key, frozenset())
    if status in _GRANTING_STATUSES:
        return granted
    if status in _UNPAID_STATUSES:
        return granted & _FREE_MODULES
    return set()


def modules(tenant: Tenant) -> set[Module]:
    """Modules switched on for *tenant*, across its module subscriptions. Default-deny.

    A tenant can hold several: the enterprise Work client subscription, on
    which modules are items, and one per accepted module Quote. Each grants
    the modules its items carry, by its own Stripe status: active and
    trialing grant them all; past_due or unpaid (a failed payment) keeps only
    the free Work client; anything else, such as canceled, grants nothing.
    The Team subscription carries no module prices and grants no modules.
    """
    granted: set[Module] = set()
    for sub in (tenant.stripe_subscriptions or {}).values():
        granted |= _granted_by(sub.get("items"), sub.get("status"))
    return granted


def has_module(tenant: Tenant, module: Module) -> bool:
    return module in modules(tenant)


WORK_CLIENT_LOOKUP_KEY = "promptly_work_client"


def work_client_seats(tenant: Tenant) -> int:
    """Provisioned users on the tenant's Work client, 0 when it has none.

    The quantity of the Work client item, on the subscriptions that still
    grant the Work client (as modules() reads them). If more than one does,
    the largest quantity, so a second subscription never shrinks the pool.
    """
    seats = 0
    for sub in (tenant.stripe_subscriptions or {}).values():
        items = sub.get("items") or {}
        if Module.WORK_CLIENT in _granted_by(items, sub.get("status")):
            seats = max(seats, items.get(WORK_CLIENT_LOOKUP_KEY) or 0)
    return seats


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

    Two ways to pay:

    * a paid Atlas plan. A canceled subscription leaves the plan in place
      (the webhook only sets the status), so the status is checked too. A
      paid plan with no status is one set by hand and counts as paid. The
      Atlas trial does not: it is Plan.FREE until checkout;
    * a paid module (Atlas.AI, Learn, Adopt or Suite) on a module
      subscription, the enterprise one or one created by a Quote, as
      modules() reports it. The free Work client alone does not count, and
      a module subscription that is past due grants only the Work client, so
      it does not count either.
    """
    paid_plan = (
        tenant.plan is not Plan.FREE
        and tenant.subscription_status is not SubscriptionStatus.CANCELED
    )
    return paid_plan or bool(modules(tenant) - _FREE_MODULES)


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


def free_client_days_left(grace: FreeClientGrace, *, now: datetime) -> int | None:
    """Whole days until the 90 end, rounded up; 0 once they have; None before day 0.

    An organisation-wide fact, the one Promptly Guide reads with the status
    (`GET /guide/work-client`) to tell admins when the hosted model stops.
    `now` MUST be the DB server's now(), as for free_client_grace().
    """
    if grace.grace_ends_at is None:
        return None
    return max(math.ceil((grace.grace_ends_at - now) / timedelta(days=1)), 0)
