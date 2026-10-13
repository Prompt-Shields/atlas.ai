"""Plan → capability gate and module entitlements. Pure functions, no I/O, no DB writes."""

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
