from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.prompt_event import PromptEvent
from app.schemas.telemetry import PromptEventKind, PromptEventSeverity, PromptEventSource
from app.services.device_risk_engine import RiskEngineConfig, high_risk_user_ids
from tests.conftest import TEST_TENANT_ID, TestSessionLocal

pytestmark = [pytest.mark.integration]


def _violation(user: str, sev, occ: int, ago_h: int) -> PromptEvent:
    return PromptEvent(
        id=uuid.uuid4(),
        tenant_id=TEST_TENANT_ID,
        source=PromptEventSource.MACOS_WIDGET,
        event_kind=PromptEventKind.VIOLATION,
        severity=sev,
        user_external_id=user,
        occurrences=occ,
        occurred_at=datetime.now(UTC) - timedelta(hours=ago_h),
    )


@pytest.mark.asyncio
async def test_high_risk_users_over_threshold_only():
    cfg = RiskEngineConfig(min_violations=5, window_hours=24)
    async with TestSessionLocal() as s:
        s.add_all(
            [
                _violation("risky@x.io", PromptEventSeverity.HIGH, occ=4, ago_h=1),
                _violation("risky@x.io", PromptEventSeverity.CRITICAL, occ=3, ago_h=2),  # 7 >= 5
                _violation("safe@x.io", PromptEventSeverity.HIGH, occ=2, ago_h=1),
                _violation(
                    "old@x.io", PromptEventSeverity.CRITICAL, occ=9, ago_h=48
                ),  # outside window
                _violation("lowsev@x.io", PromptEventSeverity.LOW, occ=9, ago_h=1),  # low severity
            ]
        )
        await s.commit()
        users = await high_risk_user_ids(s, TEST_TENANT_ID, cfg)
        assert "risky@x.io" in users
        assert "safe@x.io" not in users
        assert "old@x.io" not in users
        assert "lowsev@x.io" not in users
