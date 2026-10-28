# backend/tests/unit/test_entitlements.py
from datetime import UTC, datetime, timedelta

import pytest

from app.models.tenant import Plan, SubscriptionStatus, Tenant
from app.services.entitlements import (
    FREE_CLIENT_GRACE_DAYS,
    GRACE_ENDING_WARNING_DAYS,
    LOOKUP_KEY_MODULES,
    PLAN_ENTITLEMENTS,
    Capability,
    FreeClientStatus,
    Module,
    can,
    free_client_days_left,
    free_client_grace,
    hard_locked,
    has_module,
    modules,
    paid_module_active,
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


# One fixed clock for every test that passes `now`. Reading datetime.now() once for
# the trial end and again for `now` let the two drift apart: a run slower than the
# 1 ms margin saw the trial as already over.
_NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def test_hard_locked_true_after_trial_no_subscription():
    past = _NOW - timedelta(milliseconds=1)
    t = _tenant(trial_ends_at=past, stripe_subscription_id=None)
    assert hard_locked(t, now=_NOW) is True


def test_hard_locked_false_just_before_expiry():
    future = _NOW + timedelta(milliseconds=1)
    t = _tenant(trial_ends_at=future, stripe_subscription_id=None)
    assert hard_locked(t, now=_NOW) is False


def test_hard_locked_false_when_subscription_attached():
    past = _NOW - timedelta(days=1)
    t = _tenant(trial_ends_at=past, stripe_subscription_id="sub_123")
    assert hard_locked(t, now=_NOW) is False


def test_hard_locked_false_when_no_trial_set():
    t = _tenant(trial_ends_at=None, stripe_subscription_id=None)
    assert hard_locked(t, now=_NOW) is False


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


# ---------------------------------------------------------------------------
# Free Work client: the 90-day rule (promptly-guide#52)
# ---------------------------------------------------------------------------


def _started(days_ago: float, **kw) -> Tenant:
    return _tenant(
        plan=kw.pop("plan", Plan.FREE),
        subscription_status=kw.pop("subscription_status", None),
        work_client_started_at=_NOW - timedelta(days=days_ago),
        own_endpoint_declared_at=kw.pop("own_endpoint_declared_at", None),
        **kw,
    )


def test_no_status_before_the_work_client_starts():
    g = free_client_grace(_tenant(plan=Plan.FREE, work_client_started_at=None), now=_NOW)
    assert g.status is None
    assert g.days_since_start is None
    assert g.grace_ends_at is None


def test_active_inside_the_grace():
    g = free_client_grace(_started(10), now=_NOW)
    assert g.status is FreeClientStatus.ACTIVE
    assert g.days_since_start == 10
    assert g.grace_ends_at == _NOW + timedelta(days=FREE_CLIENT_GRACE_DAYS - 10)


def test_grace_ending_in_the_warning_window():
    first = FREE_CLIENT_GRACE_DAYS - GRACE_ENDING_WARNING_DAYS
    assert free_client_grace(_started(first - 0.01), now=_NOW).status is FreeClientStatus.ACTIVE
    assert free_client_grace(_started(first), now=_NOW).status is FreeClientStatus.GRACE_ENDING
    assert free_client_grace(_started(89.99), now=_NOW).status is FreeClientStatus.GRACE_ENDING


def test_endpoint_required_from_day_90():
    g = free_client_grace(_started(FREE_CLIENT_GRACE_DAYS), now=_NOW)
    assert g.status is FreeClientStatus.ENDPOINT_REQUIRED
    assert g.days_since_start == 90
    assert free_client_grace(_started(400), now=_NOW).status is FreeClientStatus.ENDPOINT_REQUIRED


def test_own_endpoint_keeps_the_client_after_day_90():
    t = _started(120, own_endpoint_declared_at=_NOW - timedelta(days=1))
    g = free_client_grace(t, now=_NOW)
    assert g.status is FreeClientStatus.ACTIVE
    assert g.own_endpoint_declared is True


def test_paid_module_keeps_the_client_after_day_90():
    t = _started(120, plan=Plan.TEAM, subscription_status=SubscriptionStatus.ACTIVE)
    g = free_client_grace(t, now=_NOW)
    assert g.status is FreeClientStatus.ACTIVE
    assert g.paid_module_active is True


def test_paid_module_suppresses_the_warning():
    t = _started(80, plan=Plan.TEAM, subscription_status=SubscriptionStatus.PAST_DUE)
    assert free_client_grace(t, now=_NOW).status is FreeClientStatus.ACTIVE


def test_canceled_module_no_longer_counts():
    t = _started(120, plan=Plan.TEAM, subscription_status=SubscriptionStatus.CANCELED)
    g = free_client_grace(t, now=_NOW)
    assert g.paid_module_active is False
    assert g.status is FreeClientStatus.ENDPOINT_REQUIRED


def test_paid_module_rules():
    assert paid_module_active(_tenant(plan=Plan.FREE)) is False
    # The Atlas trial is Plan.FREE until checkout, so it is not a paid module.
    trial = _tenant(plan=Plan.FREE, subscription_status=SubscriptionStatus.TRIALING)
    assert paid_module_active(trial) is False
    # A paid trial on a Stripe subscription is.
    paid_trial = _tenant(plan=Plan.TEAM, subscription_status=SubscriptionStatus.TRIALING)
    assert paid_module_active(paid_trial) is True
    # An enterprise plan set by hand, with no Stripe status, is.
    assert paid_module_active(_tenant(plan=Plan.ENTERPRISE, subscription_status=None)) is True


def _module_sub(status: str, *lookup_keys: str) -> dict:
    return {
        "sub_m": {"kind": "enterprise", "status": status, "items": dict.fromkeys(lookup_keys, 1)}
    }


def test_module_subscription_counts_as_a_paid_module():
    # Atlas.AI bought by Quote or on the enterprise subscription, with no paid plan.
    subs = _module_sub("active", "promptly_work_client", "promptly_atlas_annual")
    t = _started(120, stripe_subscriptions=subs)
    assert paid_module_active(t) is True
    assert free_client_grace(t, now=_NOW).status is FreeClientStatus.ACTIVE
    # Suite grants Atlas.AI, Learn and Adopt, so it counts too.
    suite = _started(120, stripe_subscriptions=_module_sub("active", "promptly_suite_annual"))
    assert paid_module_active(suite) is True


def test_work_client_subscription_alone_is_not_a_paid_module():
    t = _started(120, stripe_subscriptions=_module_sub("active", "promptly_work_client"))
    assert paid_module_active(t) is False
    assert free_client_grace(t, now=_NOW).status is FreeClientStatus.ENDPOINT_REQUIRED


def test_past_due_or_ended_module_subscription_is_not_a_paid_module():
    # past_due keeps only the free Work client; canceled grants nothing.
    for status in ("past_due", "unpaid", "canceled"):
        subs = _module_sub(status, "promptly_work_client", "promptly_atlas_annual")
        t = _started(120, stripe_subscriptions=subs)
        assert paid_module_active(t) is False, status
        assert free_client_grace(t, now=_NOW).status is FreeClientStatus.ENDPOINT_REQUIRED


def test_start_in_the_future_is_day_zero():
    g = free_client_grace(_started(-1), now=_NOW)
    assert g.days_since_start == 0
    assert g.status is FreeClientStatus.ACTIVE


def test_days_left_round_up_and_stop_at_zero():
    assert free_client_days_left(free_client_grace(_started(10), now=_NOW), now=_NOW) == 80
    assert free_client_days_left(free_client_grace(_started(89.99), now=_NOW), now=_NOW) == 1
    assert free_client_days_left(free_client_grace(_started(90), now=_NOW), now=_NOW) == 0
    assert free_client_days_left(free_client_grace(_started(400), now=_NOW), now=_NOW) == 0
    not_started = _tenant(plan=Plan.FREE, work_client_started_at=None)
    assert free_client_days_left(free_client_grace(not_started, now=_NOW), now=_NOW) is None
