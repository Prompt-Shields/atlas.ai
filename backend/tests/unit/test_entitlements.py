# backend/tests/unit/test_entitlements.py
from datetime import UTC, datetime, timedelta

import pytest

from app.models.tenant import Plan, SubscriptionStatus, Tenant
from app.services.entitlements import (
    LOOKUP_KEY_MODULES,
    PLAN_ENTITLEMENTS,
    Capability,
    Module,
    can,
    hard_locked,
    has_module,
    modules,
    subscription_items_from_stripe,
)


def _tenant(**kw) -> Tenant:
    t = Tenant(name="t", slug="t")
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def test_team_plan_grants_invites():
    t = _tenant(plan=Plan.TEAM)
    assert can(t, Capability.INVITE_USERS) is True


def test_free_plan_denies_invites():
    t = _tenant(plan=Plan.FREE)
    assert can(t, Capability.INVITE_USERS) is False


def test_unknown_capability_defaults_deny():
    # every plan maps to a set; absence => deny
    assert all(isinstance(v, set) for v in PLAN_ENTITLEMENTS.values())


def test_hard_locked_true_after_trial_no_subscription():
    past = datetime.now(UTC) - timedelta(milliseconds=1)
    t = _tenant(trial_ends_at=past, stripe_subscription_id=None)
    assert hard_locked(t, now=datetime.now(UTC)) is True


def test_hard_locked_false_just_before_expiry():
    future = datetime.now(UTC) + timedelta(milliseconds=1)
    t = _tenant(trial_ends_at=future, stripe_subscription_id=None)
    assert hard_locked(t, now=datetime.now(UTC)) is False


def test_hard_locked_false_when_subscription_attached():
    past = datetime.now(UTC) - timedelta(days=1)
    t = _tenant(trial_ends_at=past, stripe_subscription_id="sub_123")
    assert hard_locked(t, now=datetime.now(UTC)) is False


def test_hard_locked_false_when_no_trial_set():
    t = _tenant(trial_ends_at=None, stripe_subscription_id=None)
    assert hard_locked(t, now=datetime.now(UTC)) is False


# --- Modules -----------------------------------------------------------------

_ALL_PAID = {Module.ATLAS, Module.LEARN, Module.ADOPT}


def test_lookup_keys_match_the_stripe_setup():
    # These keys are created in Stripe; a typo here silently grants nothing.
    assert set(LOOKUP_KEY_MODULES) == {
        "promptly_work_client",
        "promptly_atlas_annual",
        "promptly_learn_annual",
        "promptly_adopt_annual",
        "promptly_suite_annual",
        "promptly_platform_fee_annual",
        "promptly_step_overage",
    }


def test_suite_grants_atlas_learn_and_adopt():
    assert LOOKUP_KEY_MODULES["promptly_suite_annual"] == _ALL_PAID


def test_fee_and_overage_grant_nothing():
    assert LOOKUP_KEY_MODULES["promptly_platform_fee_annual"] == frozenset()
    assert LOOKUP_KEY_MODULES["promptly_step_overage"] == frozenset()


def test_items_from_stripe_keeps_lookup_key_and_quantity():
    items = [
        {"id": "si_1", "quantity": 200, "price": {"lookup_key": "promptly_work_client"}},
        {"id": "si_2", "quantity": 50, "price": {"lookup_key": "promptly_atlas_annual"}},
        {"id": "si_3", "price": {"lookup_key": "promptly_step_overage"}},  # metered
        {"id": "si_4", "quantity": 5, "price": {"lookup_key": None}},  # Atlas Team price
        {"id": "si_5", "quantity": 5},
    ]
    assert subscription_items_from_stripe(items) == {
        "promptly_work_client": 200,
        "promptly_atlas_annual": 50,
        "promptly_step_overage": None,
    }


def _subs(*entries: tuple[str, dict]) -> dict:
    return {
        f"sub_{i}": {"kind": "enterprise", "status": status, "items": items}
        for i, (status, items) in enumerate(entries)
    }


def test_active_subscription_grants_its_modules():
    t = _tenant(
        stripe_subscriptions=_subs(
            ("active", {"promptly_work_client": 10, "promptly_learn_annual": 10})
        )
    )
    assert modules(t) == {Module.WORK_CLIENT, Module.LEARN}
    assert has_module(t, Module.LEARN) is True
    assert has_module(t, Module.ATLAS) is False


def test_trialing_subscription_grants_its_modules():
    t = _tenant(stripe_subscriptions=_subs(("trialing", {"promptly_suite_annual": 10})))
    assert modules(t) == _ALL_PAID


@pytest.mark.parametrize("status", ["past_due", "unpaid"])
def test_unpaid_subscription_keeps_only_the_work_client(status):
    t = _tenant(
        stripe_subscriptions=_subs(
            (status, {"promptly_work_client": 10, "promptly_suite_annual": 10})
        )
    )
    assert modules(t) == {Module.WORK_CLIENT}


@pytest.mark.parametrize("status", ["canceled", "incomplete_expired", "paused", None])
def test_ended_subscription_grants_nothing(status):
    t = _tenant(
        stripe_subscriptions=_subs(
            (status, {"promptly_work_client": 10, "promptly_suite_annual": 10})
        )
    )
    assert modules(t) == set()


def test_modules_add_up_across_subscriptions():
    # Work client subscription with Atlas.AI, plus a quote's subscription for Learn.
    t = _tenant(
        stripe_subscriptions=_subs(
            ("active", {"promptly_work_client": 200, "promptly_atlas_annual": 50}),
            ("active", {"promptly_learn_annual": 50, "promptly_platform_fee_annual": 1}),
        )
    )
    assert modules(t) == {Module.WORK_CLIENT, Module.ATLAS, Module.LEARN}


def test_each_subscription_is_judged_by_its_own_status():
    t = _tenant(
        stripe_subscriptions=_subs(
            ("active", {"promptly_work_client": 200, "promptly_atlas_annual": 50}),
            ("past_due", {"promptly_learn_annual": 50}),
        )
    )
    assert modules(t) == {Module.WORK_CLIENT, Module.ATLAS}


def test_team_status_does_not_affect_modules():
    # The Team subscription's status is for the Team plan only.
    t = _tenant(
        subscription_status=SubscriptionStatus.CANCELED,
        stripe_subscriptions=_subs(("active", {"promptly_adopt_annual": 10})),
    )
    assert modules(t) == {Module.ADOPT}


def test_unknown_lookup_key_grants_nothing():
    t = _tenant(stripe_subscriptions=_subs(("active", {"promptly_mystery_annual": 10})))
    assert modules(t) == set()


def test_tenant_without_module_subscriptions_has_no_modules():
    # A Team-plan tenant from before modules existed: unchanged behaviour.
    t = _tenant(plan=Plan.TEAM, subscription_status=SubscriptionStatus.ACTIVE)
    assert modules(t) == set()
