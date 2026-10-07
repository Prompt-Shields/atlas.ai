"""`/api/v1/guide` end to end on the sqlite harness (promptly-guide #37, #38).

What these defend: a Guide Mac reaches Atlas with its Firebase token alone and no
device row; a person counts once a month; nothing about them is stored; and the
only way figures leave is through the gate, tenant by tenant.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

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

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

OTHER_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-0000000000b2")
OTHER_PROJECT = "someone-elses-guide"


def _last_month() -> dict[str, int]:
    now = datetime.now(UTC)
    year, month = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
    return {"year": year, "month": month}


def _period_text() -> str:
    p = _last_month()
    return f"{p['year']:04d}-{p['month']:02d}"


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
        json={"team": team, "period": _last_month(), "reached": reached},
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
        json={"team": "sales", "period": _last_month(), "reached": [], "email": "kari@example.com"},
        headers=_guide(),
    )
    assert r.status_code == 422


async def test_ten_people_make_a_figure_and_nine_do_not(client: AsyncClient) -> None:
    await _connect(client, ["app", "completion"])
    for i in range(10):
        await _contribute(client, f"uid-{i}", [CLAUDE], team="sales")
    for i in range(9):
        await _contribute(client, f"uid-small-{i}", [CLAUDE, FINISHED], team="legal")
    r = await client.get(
        "/api/v1/guide/adoption/report",
        params={"period": _period_text()},
        headers=_admin(TEST_TENANT_ID),
    )
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
    assert body["teams_too_small"] == ["legal"]
    assert "chose to be counted" in body["note"]


async def test_one_tenant_never_sees_anothers_figures(client: AsyncClient) -> None:
    await _connect(client, ["app"])
    await _connect(client, ["app"], tenant_id=OTHER_TENANT_ID, project=OTHER_PROJECT)
    for i in range(10):
        await _contribute(client, f"uid-{i}", [CLAUDE])
    r = await client.get(
        "/api/v1/guide/adoption/report",
        params={"period": _period_text()},
        headers=_admin(OTHER_TENANT_ID),
    )
    assert r.json()["figures"] == []
    assert r.json()["teams_too_small"] == []


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
            params={"period": _period_text(), parameter: "kari@example.com"},
            headers=_admin(TEST_TENANT_ID),
        )
        assert r.status_code == 400, (path, r.text)
        assert r.json()["error"]["code"] == "ADOPTION_IS_BY_TEAM"


async def test_the_minimum_group_size_is_ten_as_guide_pins_it() -> None:
    from app.services import guide_adoption_service as adoption

    # Lowering it is a change to Guide's gate and this port together, for months after
    # the change only (promptly-guide docs/adoption-analytics.md).
    assert adoption.MINIMUM_GROUP_SIZE == 10
