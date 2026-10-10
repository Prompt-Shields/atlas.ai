"""The 30-day pilot report (promptly-guide #39).

What these defend: the report is not there before 30 days; Guide's sections are its
gated figures and nothing finer; the risk section shows only rows spanning enough
devices; and one tenant's report never carries another's data.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from app.auth.jwt import create_access_token
from app.models.guide_adoption import (
    GuideAdoptionContributors,
    GuideAdoptionCount,
    GuideConnection,
)
from app.models.prompt_event import PromptEvent
from app.schemas.telemetry import PromptEventAction, PromptEventKind, PromptEventSource
from app.services import guide_pilot_report as pilot
from tests.conftest import TEST_TENANT_ID, TestSessionLocal, auth_header, ensure_tenant
from tests.unit.guide_db import guide_resolvers  # noqa: F401

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

OTHER_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-0000000000b3")


def _admin(tenant_id: uuid.UUID = TEST_TENANT_ID) -> dict[str, str]:
    return auth_header(
        create_access_token(
            user_id=uuid.uuid4(),
            email="analyst@example.com",
            roles=["ANALYST"],
            tenant_id=tenant_id,
            org_id=tenant_id,
        )
    )


def _last_month(now: datetime) -> str:
    year, month = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
    return f"{year:04d}-{month:02d}"


async def _connect(
    days_ago: int,
    tenant_id: uuid.UUID = TEST_TENANT_ID,
    project: str = "p",
    kinds: tuple[str, ...] = ("app", "completion", "topic"),
) -> None:
    async with TestSessionLocal() as s:
        await ensure_tenant(s, tenant_id, name="Nordlys")
        s.add(
            GuideConnection(
                tenant_id=tenant_id,
                firebase_project_id=project,
                firebase_tenant_id="",
                offered_kinds=list(kinds),
                created_at=datetime.now(UTC) - timedelta(days=days_ago),
            )
        )
        await s.commit()


async def _team(
    period: str,
    team: str,
    people: int,
    reached: dict[tuple[str, str], int],
    tenant_id: uuid.UUID = TEST_TENANT_ID,
) -> None:
    async with TestSessionLocal() as s:
        s.add(
            GuideAdoptionContributors(tenant_id=tenant_id, period=period, team=team, people=people)
        )
        for (kind, category_id), n in reached.items():
            s.add(
                GuideAdoptionCount(
                    tenant_id=tenant_id,
                    period=period,
                    team=team,
                    category_kind=kind,
                    category_id=category_id,
                    people=n,
                )
            )
        await s.commit()


async def _violations(
    devices: int,
    app: str,
    category: str,
    action: PromptEventAction | None,
    tenant_id: uuid.UUID = TEST_TENANT_ID,
) -> None:
    async with TestSessionLocal() as s:
        for i in range(devices):
            s.add(
                PromptEvent(
                    tenant_id=tenant_id,
                    source=PromptEventSource.SAFARI_EXTENSION,
                    event_kind=PromptEventKind.VIOLATION,
                    app_id=app,
                    action=action,
                    pii_categories={category: 1},
                    device_fingerprint=f"{app}-device-{i}",
                    occurrences=2,
                    occurred_at=datetime.now(UTC) - timedelta(days=3),
                )
            )
        await s.commit()


async def test_no_connection_no_report(client: AsyncClient) -> None:
    r = await client.get("/api/v1/guide/pilot-report", headers=_admin())
    assert r.status_code == 404


async def test_the_report_waits_for_thirty_days(client: AsyncClient) -> None:
    await _connect(days_ago=10)
    r = await client.get("/api/v1/guide/pilot-report", headers=_admin())
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "PILOT_NOT_READY"


async def test_guides_sections_are_its_gated_figures(client: AsyncClient) -> None:
    await _connect(days_ago=40)
    period = _last_month(datetime.now(UTC))
    await _team(
        period,
        "sales",
        12,
        {
            ("app", "Claude"): 12,
            ("completion", "finished"): 10,
            ("completion", "stuck"): 3,  # too few people: suppressed
            ("topic", "chatgpt/model"): 11,
        },
    )
    await _team(period, "legal", 5, {("app", "Claude"): 5})
    r = await client.get("/api/v1/guide/pilot-report", headers=_admin())
    assert r.status_code == 200, r.text
    month = next(m for m in r.json()["months"] if m["period"] == period)
    assert [(f["team"], f["category_id"], f["band"]) for f in month["tools"]] == [
        ("sales", "Claude", "80% or more")
    ]
    assert [f["band"] for f in month["finished"]] == ["80% or more"]
    assert month["not_finished"] == []
    assert [f["category_id"] for f in month["topics"]] == ["chatgpt/model"]
    assert month["teams_too_small"] == ["legal"]
    assert month["suppressed_categories"] == {"sales": 1}


async def test_where_people_get_stuck_in_a_task_is_its_own_section(client: AsyncClient) -> None:
    # promptly-guide #85: where a topic's walkthrough ended, through the same gate.
    await _connect(days_ago=40, kinds=("completion", "friction"))
    period = _last_month(datetime.now(UTC))
    await _team(
        period,
        "finance",
        20,
        {
            ("friction", "nordlys-expenses/new-claim/step-3"): 11,
            ("friction", "nordlys-expenses/new-claim/finished"): 4,  # too few: suppressed
        },
    )
    r = await client.get("/api/v1/guide/pilot-report", headers=_admin())
    assert r.status_code == 200, r.text
    month = next(m for m in r.json()["months"] if m["period"] == period)
    assert [(f["team"], f["category_id"], f["band"]) for f in month["friction"]] == [
        ("finance", "nordlys-expenses/new-claim/step-3", "50\u201359%")
    ]
    assert month["suppressed_categories"] == {"finance": 1}

    r = await client.get(
        "/api/v1/guide/pilot-report", params={"format": "markdown"}, headers=_admin()
    )
    assert "Where people get stuck in a task" in r.text
    assert "nordlys-expenses/new-claim: stopped at step 3" in r.text


async def test_friction_is_not_shown_when_the_organisation_does_not_count_it(
    client: AsyncClient,
) -> None:
    await _connect(days_ago=40)
    period = _last_month(datetime.now(UTC))
    await _team(period, "finance", 20, {("friction", "nordlys-expenses/new-claim/step-3"): 15})
    r = await client.get("/api/v1/guide/pilot-report", headers=_admin())
    month = next(m for m in r.json()["months"] if m["period"] == period)
    assert month["friction"] == []


async def test_a_friction_label_says_finished_or_the_step() -> None:
    assert pilot.friction_label("a/b/finished") == "a/b: finished"
    assert pilot.friction_label("a/b/step-10") == "a/b: stopped at step 10"


async def test_risk_rows_from_too_few_devices_are_left_out(client: AsyncClient) -> None:
    await _connect(days_ago=40)
    await _violations(10, "chatgpt.com", "email", PromptEventAction.BLOCKED)
    await _violations(2, "intranet.example", "national_id", None)
    r = await client.get("/api/v1/guide/pilot-report", headers=_admin())
    risk = r.json()["risk"]
    assert risk["by_category"] == [{"key": "email", "events": 20, "devices": 10}]
    assert risk["by_app"] == [{"key": "chatgpt.com", "events": 20, "devices": 10}]
    assert risk["by_action"] == [{"key": "blocked", "events": 20, "devices": 10}]
    assert risk["suppressed"] == {"category": 1, "app": 1, "action": 0}
    assert "device" not in str(risk["by_app"]).replace("devices", "")


async def test_one_tenant_never_sees_anothers(client: AsyncClient) -> None:
    await _connect(days_ago=40)
    await _connect(days_ago=40, tenant_id=OTHER_TENANT_ID, project="q")
    period = _last_month(datetime.now(UTC))
    await _team(period, "sales", 12, {("app", "Claude"): 12})
    await _violations(10, "chatgpt.com", "email", PromptEventAction.BLOCKED)
    r = await client.get("/api/v1/guide/pilot-report", headers=_admin(OTHER_TENANT_ID))
    body = r.json()
    assert all(m["tools"] == [] for m in body["months"])
    assert body["risk"]["by_app"] == []


async def test_the_markdown_is_the_same_report(client: AsyncClient) -> None:
    await _connect(days_ago=40)
    period = _last_month(datetime.now(UTC))
    await _team(period, "sales", 12, {("app", "Claude"): 12})
    await _violations(10, "chatgpt.com", "email", PromptEventAction.BLOCKED)
    r = await client.get(
        "/api/v1/guide/pilot-report", params={"format": "markdown"}, headers=_admin()
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    text = r.text
    assert text.startswith("# Promptly Guide pilot report: Nordlys")
    assert "| sales | Claude | 80% or more |" in text
    assert "| email | 20 | 10 |" in text
    assert "chose to be counted" in text
    assert "chatgpt.com-device-" not in text


async def test_the_months_are_the_finished_ones_since_connecting() -> None:
    connected = datetime(2026, 7, 20, tzinfo=UTC)
    assert pilot.report_months(connected, datetime(2026, 8, 25, tzinfo=UTC)) == ["2026-07"]
    assert pilot.report_months(connected, datetime(2026, 12, 2, tzinfo=UTC)) == [
        "2026-09",
        "2026-10",
        "2026-11",
    ]
    assert pilot.report_months(
        datetime(2025, 12, 5, tzinfo=UTC), datetime(2026, 1, 9, tzinfo=UTC)
    ) == ["2025-12"]
