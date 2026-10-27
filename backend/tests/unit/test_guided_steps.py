"""Unit tests for guided-step caps (promptly-guide#62).

The allowances are commercial terms and come from configuration, so these
tests use made-up figures (`CAPS`) and never the plan's.
"""

from __future__ import annotations

import pathlib
import re

import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.services import guided_steps
from app.services.guided_steps import (
    Decision,
    GuideTier,
    StepCaps,
    decide,
    enterprise_pool,
    step_caps,
)

pytestmark = pytest.mark.unit

CAPS = StepCaps(free_monthly=3, plus_monthly=11, pool_per_user=7)
UNSET = StepCaps()


def test_free_blocks_at_its_cap() -> None:
    assert decide(GuideTier.FREE, used=2, caps=CAPS).decision is Decision.ALLOW
    assert decide(GuideTier.FREE, used=3, caps=CAPS).decision is Decision.BLOCK


def test_plus_slows_at_its_cap_and_never_meters() -> None:
    assert decide(GuideTier.PLUS, used=10, caps=CAPS).decision is Decision.ALLOW
    v = decide(GuideTier.PLUS, used=11, caps=CAPS)
    assert v.decision is Decision.SLOW and not v.meter_step


def test_enterprise_pool_is_pooled_per_seat() -> None:
    assert enterprise_pool(10, CAPS) == 70
    assert enterprise_pool(-1, CAPS) == 0
    assert not decide(GuideTier.ENTERPRISE, used=69, seats=10, caps=CAPS).meter_step
    assert decide(GuideTier.ENTERPRISE, used=70, seats=10, caps=CAPS).meter_step


def test_own_endpoint_is_never_metered() -> None:
    v = decide(GuideTier.ENTERPRISE, used=9999, seats=1, own_endpoint=True, caps=CAPS)
    assert v.decision is Decision.ALLOW and not v.meter_step


# ── Not configured: fail closed ────────────────────────────────────────────


def test_unset_pool_is_unknown() -> None:
    assert enterprise_pool(10, UNSET) is None


@pytest.mark.parametrize("tier", list(GuideTier))
def test_a_tier_without_an_allowance_is_blocked_and_never_metered(tier: GuideTier) -> None:
    v = decide(tier, used=0, seats=10, caps=UNSET)
    assert v.decision is Decision.BLOCK and not v.meter_step


def test_the_allowances_are_read_from_the_settings(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "guided_steps_free_monthly", 3)
    monkeypatch.setattr(settings, "guided_steps_plus_monthly", 11)
    monkeypatch.setattr(settings, "guided_steps_pool_per_user", 7)
    assert step_caps() == CAPS
    assert enterprise_pool(2) == 14
    assert decide(GuideTier.FREE, used=3).decision is Decision.BLOCK


def test_the_allowances_are_unset_by_default(monkeypatch) -> None:
    for name in ("FREE_MONTHLY", "PLUS_MONTHLY", "POOL_PER_USER"):
        monkeypatch.delenv(f"GUIDED_STEPS_{name}", raising=False)
    s = Settings(_env_file=None)
    assert s.guided_steps_free_monthly is None
    assert s.guided_steps_plus_monthly is None
    assert s.guided_steps_pool_per_user is None


def test_the_allowances_come_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("GUIDED_STEPS_POOL_PER_USER", "7")
    assert Settings(_env_file=None).guided_steps_pool_per_user == 7


def test_a_negative_allowance_is_refused(monkeypatch) -> None:
    monkeypatch.setenv("GUIDED_STEPS_POOL_PER_USER", "-1")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_no_allowance_or_price_is_written_in_the_module() -> None:
    """The figures live in configuration; a number assigned here would be one of them."""
    source = pathlib.Path(guided_steps.__file__).read_text()
    assert not re.search(r"^[A-Z_]+\s*=\s*[\d.]+\s*$", source, re.MULTILINE)
    assert "overage_eur" not in source
