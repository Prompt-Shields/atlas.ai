"""SCIM provisioning end to end, as Entra drives it, through to what Guide reads
(promptly-guide #58).

`scripts/scim_entra_lifecycle.py` replays Entra's provisioning pattern for two people
and checks, after every step, the groups SCIM holds them in and the groups Guide's
`GET /api/v1/guide/groups` returns for their sign-in. The same script runs against a
deployed Atlas, where it can check the SCIM side only; here Guide's sign-ins are minted
locally, so both sides are checked.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.auth.firebase_token import get_firebase_verifier
from app.auth.jwt import create_access_token
from app.main import app
from app.models.guide_scim import GuideScimGroup, GuideScimMember, GuideScimUser
from scripts.scim_entra_lifecycle import Run, lifecycle, report
from tests.conftest import TEST_TENANT_ID, TestSessionLocal, auth_header
from tests.unit import firebase_keys as keys
from tests.unit.guide_db import guide_resolvers  # noqa: F401

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def _local_google_keys():
    local = keys.verifier()
    app.dependency_overrides[get_firebase_verifier] = lambda: local
    yield
    app.dependency_overrides.pop(get_firebase_verifier, None)


def _admin() -> dict[str, str]:
    return auth_header(
        create_access_token(
            user_id=uuid.uuid4(),
            email="admin@example.com",
            roles=["TENANT_ADMIN"],
            tenant_id=TEST_TENANT_ID,
            org_id=TEST_TENANT_ID,
        )
    )


async def _scim_headers(client: AsyncClient) -> dict[str, str]:
    r = await client.put(
        "/api/v1/guide/connection",
        json={"firebase_project_id": keys.PROJECT, "offered_kinds": []},
        headers=_admin(),
    )
    assert r.status_code == 200, r.text
    r = await client.post("/api/v1/guide/scim-token", headers=_admin())
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}", "Content-Type": "application/scim+json"}


def _guide(email: str) -> dict[str, str]:
    return auth_header(keys.token(subject=f"uid-{email}", overrides={"email": email}))


async def test_entras_provisioning_reaches_guide_at_every_step(client: AsyncClient) -> None:
    run = Run(client, await _scim_headers(client), "nordlys.example", _guide, prefix="t")
    results = await lifecycle(run)
    assert [r.name for r in results] == [
        "provisioned, in no group yet",
        "added to groups",
        "moved between groups",
        "a group renamed",
        "an email changed",
        "disabled",
        "enabled again",
        "deleted",
    ]
    assert all(r.ok for r in results), report(results)
    # Guide was asked at every step, and agreed with SCIM each time.
    assert all(r.guide == r.scim for r in results)


async def test_the_run_leaves_nothing_behind(client: AsyncClient) -> None:
    run = Run(client, await _scim_headers(client), "nordlys.example", prefix="t")
    await lifecycle(run)
    async with TestSessionLocal() as s:
        for model in (GuideScimUser, GuideScimGroup, GuideScimMember):
            assert (await s.execute(select(func.count()).select_from(model))).scalar_one() == 0


async def test_a_wrong_expectation_is_reported(client: AsyncClient) -> None:
    # The check can fail: a person expected in a group they are not in is named.
    run = Run(client, await _scim_headers(client), "nordlys.example", _guide, prefix="t")
    kari = run.email("kari")
    await run.provision_user(kari, "aad-1")
    result = await run.check("wrong on purpose", {kari: ["t Sales"]})
    await run.delete_all()
    assert not result.ok
    assert result.problems == [
        f"SCIM holds {kari} in [], expected ['t Sales']",
        f"Guide reads [] for {kari}, expected ['t Sales']",
    ]
