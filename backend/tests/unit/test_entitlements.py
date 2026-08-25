# backend/tests/unit/test_entitlements.py
from datetime import UTC, datetime, timedelta

from app.models.tenant import Plan, Tenant
from app.services.entitlements import PLAN_ENTITLEMENTS, Capability, can, hard_locked


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
