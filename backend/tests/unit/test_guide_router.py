"""`/api/v1/guide` end to end on the sqlite harness (promptly-guide #37, #38).

What these defend: a Guide Mac reaches Atlas with its Firebase token alone and no
device row; a person counts once a month; nothing about them is stored; and the
only way figures leave is through the gate, tenant by tenant.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import inspect, select

from app.auth.firebase_token import get_firebase_verifier
from app.auth.jwt import create_access_token
from app.main import app
from app.models.enrolled_device import EnrolledDevice
from app.models.guide_adoption import (
    GuideAdoptionContributors,
    GuideAdoptionCount,
    GuideAdoptionReceipt,
)
from tests.conftest import TEST_TENANT_ID, TestSessionLocal, auth_header
from tests.unit import firebase_keys as keys
from tests.unit.guide_db import (
    CLOSED,
    SEPTEMBER,
    SEPTEMBER_TEXT,
    GuideClock,
    guide_clock_fixture,  # noqa: F401
    guide_resolvers,  # noqa: F401
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio, pytest.mark.usefixtures("guide_clock")]

OTHER_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-0000000000b2")
OTHER_PROJECT = "someone-elses-guide"


@pytest.fixture(autouse=True)
def _local_google_keys():
    local = keys.verifier()
    app.dependency_overrides[get_firebase_verifier] = lambda: local
    yield
    app.dependency_overrides.pop(get_firebase_verifier, None)


def _admin(tenant_id: uuid.UUID) -> dict[str, str]:
    return auth_header(
        create_access_token(
            user_id=uuid.uuid4(),
            email="admin@example.com",
            roles=["TENANT_ADMIN"],
            tenant_id=tenant_id,
            org_id=tenant_id,
        )
    )


def _guide(subject: str = "uid-1", project: str = keys.PROJECT) -> dict[str, str]:
    return auth_header(keys.token(subject=subject, project=project))


async def _connect(
    client: AsyncClient,
    kinds: list[str],
    tenant_id: uuid.UUID = TEST_TENANT_ID,
    project: str = keys.PROJECT,
) -> None:
    r = await client.put(
        "/api/v1/guide/connection",
        json={"firebase_project_id": project, "offered_kinds": kinds},
        headers=_admin(tenant_id),
    )
    assert r.status_code == 200, r.text


async def _contribute(client: AsyncClient, subject: str, reached: list[dict], team: str = "sales"):
    return await client.post(
        "/api/v1/guide/adoption",
        json={"team": team, "period": SEPTEMBER, "reached": reached},
        headers=_guide(subject),
    )


CLAUDE = {"kind": "app", "id": "Claude"}
FINISHED = {"kind": "completion", "id": "finished"}


async def test_an_unconnected_project_is_not_let_in(client: AsyncClient) -> None:
    r = await client.get("/api/v1/guide/offer", headers=_guide())
    assert r.status_code == 401


async def test_guide_learns_what_is_offered(client: AsyncClient) -> None:
    await _connect(client, ["completion", "app"])
    r = await client.get("/api/v1/guide/offer", headers=_guide())
    assert r.status_code == 200
    assert r.json() == {"offered_kinds": ["app", "completion"], "minimum_group_size": 10}


async def test_an_atlas_jwt_is_not_a_guide_token(client: AsyncClient) -> None:
    await _connect(client, ["app"])
    r = await client.get("/api/v1/guide/offer", headers=_admin(TEST_TENANT_ID))
    assert r.status_code == 401


async def test_a_person_counts_once_a_month(client: AsyncClient) -> None:
    await _connect(client, ["app"])
    first = await _contribute(client, "uid-1", [CLAUDE])
    again = await _contribute(client, "uid-1", [CLAUDE])
    assert first.json() == {"counted": True}
    assert again.json() == {"counted": False}
    async with TestSessionLocal() as s:
        people = (await s.execute(select(GuideAdoptionContributors.people))).scalars().all()
        counts = (await s.execute(select(GuideAdoptionCount.people))).scalars().all()
    assert people == [1]
    assert counts == [1]


async def test_nothing_about_the_person_is_stored(client: AsyncClient) -> None:
    await _connect(client, ["app"])
    await _contribute(client, "uid-kari", [CLAUDE])
    async with TestSessionLocal() as s:
        receipts = (await s.execute(select(GuideAdoptionReceipt))).scalars().all()
        devices = (await s.execute(select(EnrolledDevice))).scalars().all()
    assert devices == []
    assert len(receipts) == 1
    stored = " ".join(
        str(getattr(receipts[0], c.key)) for c in inspect(GuideAdoptionReceipt).mapper.column_attrs
    )
    assert "uid-kari" not in stored
    assert "kari@example.com" not in stored
    for model in (GuideAdoptionContributors, GuideAdoptionCount, GuideAdoptionReceipt):
        columns = {c.key for c in inspect(model).mapper.column_attrs}
        assert not columns & {"created_at", "updated_at", "user_external_id", "email", "device_id"}


async def test_a_kind_not_offered_is_refused(client: AsyncClient) -> None:
    await _connect(client, ["completion"])
    r = await _contribute(client, "uid-1", [CLAUDE])
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "CONTRIBUTION_REJECTED"


async def test_an_extra_field_is_refused(client: AsyncClient) -> None:
    await _connect(client, ["app"])
    r = await client.post(
        "/api/v1/guide/adoption",
        json={"team": "sales", "period": SEPTEMBER, "reached": [], "email": "kari@example.com"},
        headers=_guide(),
    )
    assert r.status_code == 422


async def _report(client: AsyncClient, tenant_id: uuid.UUID = TEST_TENANT_ID):
    return await client.get(
        "/api/v1/guide/adoption/report",
        params={"period": SEPTEMBER_TEXT},
        headers=_admin(tenant_id),
    )


async def test_ten_people_make_a_figure_and_nine_do_not(
    client: AsyncClient, guide_clock: GuideClock
) -> None:
    await _connect(client, ["app", "completion"])
    for i in range(10):
        await _contribute(client, f"uid-{i}", [CLAUDE], team="sales")
    for i in range(9):
        await _contribute(client, f"uid-small-{i}", [CLAUDE, FINISHED], team="legal")
    guide_clock.now = CLOSED
    r = await _report(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["figures"] == [
        {
            "team": "sales",
            "category_kind": "app",
            "category_id": "Claude",
            "band": "80% or more",
            "band_lower": 80,
        }
    ]
    assert "legal" not in r.text  # a team too small is never named (#28)
    assert "teams_too_small_count" not in body  # nor counted
    assert body["small_teams_note"] == "Teams with fewer than 10 counted people are not shown."
    assert "chose to be counted" in body["note"]


async def test_the_small_teams_note_is_the_same_with_none_or_many(
    client: AsyncClient, guide_clock: GuideClock
) -> None:
    """An organisation that put one person in a team of their own learns nothing from
    the note: it reads the same whether that team, or any, was too small."""
    await _connect(client, ["app"])
    await _connect(client, ["app"], tenant_id=OTHER_TENANT_ID, project=OTHER_PROJECT)
    for i in range(10):
        await _contribute(client, f"uid-{i}", [CLAUDE], team="sales")
    await _contribute(client, "uid-alone", [CLAUDE], team="a team of one")
    for team in ("legal", "board", "audit"):
        await _contribute(client, f"uid-{team}", [CLAUDE], team=team)
    guide_clock.now = CLOSED
    many = (await _report(client)).json()
    none = (await _report(client, OTHER_TENANT_ID)).json()
    assert many["small_teams_note"] == none["small_teams_note"]
    assert set(many) == set(none)
    assert {f["team"] for f in many["figures"]} == {"sales"}


async def test_one_tenant_never_sees_anothers_figures(
    client: AsyncClient, guide_clock: GuideClock
) -> None:
    await _connect(client, ["app"])
    await _connect(client, ["app"], tenant_id=OTHER_TENANT_ID, project=OTHER_PROJECT)
    for i in range(10):
        await _contribute(client, f"uid-{i}", [CLAUDE])
    guide_clock.now = CLOSED
    r = await _report(client, OTHER_TENANT_ID)
    assert r.json()["figures"] == []


# ── Intake closes, then the month is shown (promptly-guide #27, E4) ──────────


async def test_a_month_is_not_shown_while_it_is_being_collected(
    client: AsyncClient, guide_clock: GuideClock
) -> None:
    await _connect(client, ["app"])
    for i in range(10):
        await _contribute(client, f"uid-{i}", [CLAUDE])
    r = await _report(client)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "MONTH_STILL_COLLECTING"
    assert r.json()["error"]["details"] == {"shown_from": "2026-10-07"}
    assert "Claude" not in r.text
    guide_clock.now = CLOSED
    assert (await _report(client)).status_code == 200


async def test_the_running_month_is_not_shown_either(
    client: AsyncClient, guide_clock: GuideClock
) -> None:
    await _connect(client, ["app"])
    guide_clock.now = CLOSED
    r = await client.get(
        "/api/v1/guide/adoption/report",
        params={"period": "2026-10"},
        headers=_admin(TEST_TENANT_ID),
    )
    assert r.status_code == 409
    assert r.json()["error"]["details"] == {"shown_from": "2026-11-07"}


async def test_after_intake_closes_nothing_more_is_counted(
    client: AsyncClient, guide_clock: GuideClock
) -> None:
    """Reading the report and then sending one more cannot show what that one Mac
    adds: once the month is shown, the Mac is refused and the figures stay as read."""
    await _connect(client, ["app"])
    for i in range(9):
        await _contribute(client, f"uid-{i}", [CLAUDE])
    guide_clock.now = CLOSED
    before = await _report(client)
    late = await _contribute(client, "uid-tenth", [CLAUDE])
    assert late.status_code == 422, late.text
    assert late.json()["error"]["code"] == "INTAKE_CLOSED"
    after = await _report(client)
    assert before.json() == after.json()
    assert after.json()["figures"] == []
    async with TestSessionLocal() as s:
        people = (await s.execute(select(GuideAdoptionContributors.people))).scalars().all()
        receipts = (await s.execute(select(GuideAdoptionReceipt))).scalars().all()
    assert people == [9]
    assert len(receipts) == 9


async def test_the_last_moment_before_closing_is_still_open(
    client: AsyncClient, guide_clock: GuideClock
) -> None:
    await _connect(client, ["app"])
    guide_clock.now = CLOSED - timedelta(seconds=1)
    assert (await _contribute(client, "uid-1", [CLAUDE])).json() == {"counted": True}
    assert (await _report(client)).status_code == 409


async def test_a_project_belongs_to_one_tenant(client: AsyncClient) -> None:
    await _connect(client, ["app"])
    r = await client.put(
        "/api/v1/guide/connection",
        json={"firebase_project_id": keys.PROJECT, "offered_kinds": ["app"]},
        headers=_admin(OTHER_TENANT_ID),
    )
    assert r.status_code == 409


async def test_only_an_admin_connects_guide(client: AsyncClient, viewer_token: str) -> None:
    r = await client.put(
        "/api/v1/guide/connection",
        json={"firebase_project_id": keys.PROJECT, "offered_kinds": ["app"]},
        headers=auth_header(viewer_token),
    )
    assert r.status_code == 403


# ── Approved tools for steering (promptly-guide #57) ─────────────────────────


async def _use_case(tool: str, status, classes: str, tenant_id: uuid.UUID = TEST_TENANT_ID) -> None:
    from app.models.use_case import UseCase, UseCaseSource

    async with TestSessionLocal() as s:
        s.add(
            UseCase(
                tenant_id=tenant_id,
                title=f"{tool} use",
                tool=tool,
                department="Sales",
                status=status,
                source=UseCaseSource.FORM,
                data_classes=classes,
            )
        )
        await s.commit()


async def test_guide_reads_the_tools_active_use_cases_name(client: AsyncClient) -> None:
    from app.models.use_case import UseCaseStatus

    await _connect(client, ["app"])
    await _use_case("Microsoft Copilot", UseCaseStatus.ACTIVE, '["customer_pii"]')
    await _use_case("microsoft copilot", UseCaseStatus.ACTIVE, '["proprietary_code", "made_up"]')
    await _use_case("ChatGPT", UseCaseStatus.DRAFT, "[]")
    await _use_case("Claude", UseCaseStatus.RETIRED, "[]")
    await _use_case("Gemini", UseCaseStatus.ACTIVE, "not json")
    await _use_case("Le Chat", UseCaseStatus.ACTIVE, "[]", tenant_id=OTHER_TENANT_ID)
    r = await client.get("/api/v1/guide/approved-tools", headers=_guide())
    assert r.status_code == 200, r.text
    assert r.json() == {
        "tools": [
            {"name": "Gemini", "data_classes": []},
            {"name": "Microsoft Copilot", "data_classes": ["customer data", "source code"]},
        ]
    }


async def test_approved_tools_need_a_guide_token(client: AsyncClient) -> None:
    await _connect(client, ["app"])
    r = await client.get("/api/v1/guide/approved-tools", headers=_admin(TEST_TENANT_ID))
    assert r.status_code == 401


# ── When a customer asks for more (promptly-guide #38) ───────────────────────


@pytest.mark.parametrize("parameter", ["user", "email", "Device_ID", "person"])
async def test_a_figure_about_one_person_is_refused_not_answered_empty(
    client: AsyncClient, parameter: str
) -> None:
    await _connect(client, ["app"])
    for path in ("/api/v1/guide/adoption/report", "/api/v1/guide/pilot-report"):
        r = await client.get(
            path,
            params={"period": SEPTEMBER_TEXT, parameter: "kari@example.com"},
            headers=_admin(TEST_TENANT_ID),
        )
        assert r.status_code == 400, (path, r.text)
        assert r.json()["error"]["code"] == "ADOPTION_IS_BY_TEAM"


async def test_the_minimum_group_size_is_ten_as_guide_pins_it() -> None:
    from app.services import guide_adoption_service as adoption

    # Lowering it is a change to Guide's gate and this port together, for months after
    # the change only (promptly-guide docs/adoption-analytics.md).
    assert adoption.MINIMUM_GROUP_SIZE == 10


# ── The E2E stack's clock (GUIDE_TEST_CLOCK) ─────────────────────────────────


@pytest.mark.parametrize("enabled", [False, True])
async def test_the_test_clock_header_works_only_when_switched_on(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    from app.config import get_settings
    from app.routers import guide as guide_router

    app.dependency_overrides.pop(guide_router.guide_now, None)  # the real clock
    settings = get_settings().model_copy(update={"guide_test_clock": enabled})
    monkeypatch.setattr(guide_router, "get_settings", lambda: settings)
    await _connect(client, ["app"])
    r = await client.post(
        "/api/v1/guide/adoption",
        json={"team": "sales", "period": SEPTEMBER, "reached": [CLAUDE]},
        headers={**_guide(), guide_router.TEST_NOW_HEADER: "2026-10-03T12:00:00Z"},
    )
    if enabled:
        assert r.json() == {"counted": True}, r.text
    else:
        # Ignored: today's date decides, and September 2026 is closed by now.
        assert r.status_code == 422, r.text
        assert r.json()["error"]["code"] == "INTAKE_CLOSED"


async def test_the_test_clock_is_refused_outside_testing() -> None:
    from pydantic import ValidationError

    from app.config import Environment, Settings

    for env in (Environment.DEVELOPMENT, Environment.STAGING, Environment.PRODUCTION):
        with pytest.raises(ValidationError, match="GUIDE_TEST_CLOCK may only be set"):
            Settings(
                app_env=env,
                app_debug=False,
                database_url="postgresql+asyncpg://u:p@localhost/db",
                jwt_secret_key="x" * 40,
                backend_cors_origins="https://atlas.example",
                guide_test_clock=True,
            )
