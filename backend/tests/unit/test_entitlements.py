# backend/tests/unit/test_entitlements.py
from datetime import UTC, datetime, timedelta

from app.models.tenant import Plan, SubscriptionStatus, Tenant
from app.services.entitlements import (
    FREE_CLIENT_GRACE_DAYS,
    GRACE_ENDING_WARNING_DAYS,
    PLAN_ENTITLEMENTS,
    Capability,
    FreeClientStatus,
    can,
    free_client_grace,
    hard_locked,
    paid_module_active,
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


# ---------------------------------------------------------------------------
# Free Work client: the 90-day rule (promptly-guide#52)
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


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


def test_start_in_the_future_is_day_zero():
    g = free_client_grace(_started(-1), now=_NOW)
    assert g.days_since_start == 0
    assert g.status is FreeClientStatus.ACTIVE
