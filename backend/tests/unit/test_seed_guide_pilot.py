"""The demo Guide pilot (promptly-guide #39): seeded, the pilot report has something
to show; and it never lands on a tenant with a real Guide connection."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.models.guide_adoption import GuideAdoptionContributors, GuideConnection
from app.models.prompt_event import PromptEvent
from app.services import guide_pilot_report as pilot
from scripts.seed_guide_pilot import DemoRefused, seed_pilot
from tests.conftest import TEST_TENANT_ID, TestSessionLocal

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


async def test_the_seeded_demo_makes_a_report_with_figures() -> None:
    async with TestSessionLocal() as db:
        seeded = await seed_pilot(db, TEST_TENANT_ID, NOW, days_ago=45)
        report = await pilot.build(db, TEST_TENANT_ID, NOW)
    assert seeded.connection
    assert seeded.months == ["2026-08", "2026-09"]
    assert report is not None
    month = report.months[-1]
    assert {f.team for f in month.tools} == {"sales", "finance"}
    assert month.teams_too_small == ["legal"]
    assert any(f.category_id.endswith("/step-3") for f in month.friction)
    assert {r.key for r in report.risk.by_app} >= {"chatgpt.com", "claude.ai"}


async def test_seeding_twice_adds_nothing() -> None:
    async with TestSessionLocal() as db:
        await seed_pilot(db, TEST_TENANT_ID, NOW)
        again = await seed_pilot(db, TEST_TENANT_ID, NOW)
        months = (
            await db.execute(select(func.count()).select_from(GuideAdoptionContributors))
        ).scalar_one()
        events = (await db.execute(select(func.count()).select_from(PromptEvent))).scalar_one()
    assert (again.connection, again.months, again.events) == (False, [], 0)
    assert months == 2 * 3  # two months, three teams
    assert events == 12 * 4


async def test_a_real_connection_is_never_given_demo_data() -> None:
    async with TestSessionLocal() as db:
        db.add(
            GuideConnection(
                tenant_id=TEST_TENANT_ID, firebase_project_id="nordlys-guide", offered_kinds=["app"]
            )
        )
        await db.commit()
        with pytest.raises(DemoRefused):
            await seed_pilot(db, TEST_TENANT_ID, NOW)
        rows = (
            await db.execute(select(func.count()).select_from(GuideAdoptionContributors))
        ).scalar_one()
    assert rows == 0


async def test_a_pilot_younger_than_thirty_days_still_has_no_report() -> None:
    async with TestSessionLocal() as db:
        await seed_pilot(db, TEST_TENANT_ID, NOW, days_ago=10)
        with pytest.raises(pilot.PilotNotReady):
            await pilot.build(db, TEST_TENANT_ID, NOW)
