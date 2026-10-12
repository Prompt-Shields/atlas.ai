"""Unit tests for guided-step caps (promptly-guide#62)."""

from __future__ import annotations

import pytest

from app.services.guided_steps import (
    Decision,
    GuideTier,
    decide,
    enterprise_pool,
    overage_eur,
)

pytestmark = pytest.mark.unit


def test_free_blocks_at_25() -> None:
    assert decide(GuideTier.FREE, used=24).decision is Decision.ALLOW
    assert decide(GuideTier.FREE, used=25).decision is Decision.BLOCK


def test_plus_slows_at_400_and_never_meters() -> None:
    assert decide(GuideTier.PLUS, used=399).decision is Decision.ALLOW
    v = decide(GuideTier.PLUS, used=400)
    assert v.decision is Decision.SLOW and not v.meter_step


def test_enterprise_pool_is_pooled_per_seat() -> None:
    assert enterprise_pool(10) == 500
    assert not decide(GuideTier.ENTERPRISE, used=499, seats=10).meter_step
    assert decide(GuideTier.ENTERPRISE, used=500, seats=10).meter_step


def test_own_endpoint_is_never_metered() -> None:
    v = decide(GuideTier.ENTERPRISE, used=9999, seats=1, own_endpoint=True)
    assert v.decision is Decision.ALLOW and not v.meter_step


def test_overage_price() -> None:
    assert overage_eur(500, 10) == 0.0
    assert overage_eur(600, 10) == 4.0
    assert overage_eur(10, 0) == 0.4
