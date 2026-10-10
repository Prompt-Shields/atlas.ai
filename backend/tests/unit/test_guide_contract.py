"""The contract with Promptly Guide, from Atlas's side (promptly-guide #37, #57, #58).

`tests/contracts/atlas-guide.json` is a copy of promptly-guide's
`contracts/atlas-guide.json`: every request Guide sends to `/api/v1/guide` and what
Guide reads from the reply. Guide's tests check that it builds exactly those requests
and reads those replies; these check that Atlas, set up as each interaction's `given`
says, answers them that way. Change the file in promptly-guide first, then copy it here
(promptly-guide's `Scripts/check-atlas-contract.py` says whether the copies match).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.auth.firebase_token import get_firebase_verifier
from app.auth.jwt import create_access_token
from app.main import app
from tests.conftest import TEST_TENANT_ID, TestSessionLocal, auth_header
from tests.unit import firebase_keys as keys
from tests.unit.guide_db import guide_resolvers  # noqa: F401

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

CONTRACT = json.loads(
    (Path(__file__).resolve().parent.parent / "contracts" / "atlas-guide.json").read_text()
)
INTERACTIONS = CONTRACT["interactions"]


@pytest.fixture(autouse=True)
def _local_google_keys():
    local = keys.verifier()
    app.dependency_overrides[get_firebase_verifier] = lambda: local
    yield
    app.dependency_overrides.pop(get_firebase_verifier, None)


def _admin() -> dict[str, str]:
    return auth_header(
        create_access_token(
            user_id=TEST_TENANT_ID,
            email="admin@example.com",
            roles=["TENANT_ADMIN"],
            tenant_id=TEST_TENANT_ID,
            org_id=TEST_TENANT_ID,
        )
    )


def _last_finished_month() -> dict[str, int]:
    now = datetime.now(UTC)
    year, month = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
    return {"year": year, "month": month}


def holds(actual: object, expected: object) -> bool:
    """Whether `actual` holds `expected`: an object may have more keys, the rest is exact."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            k in actual and holds(actual[k], v) for k, v in expected.items()
        )
    return actual == expected


async def _use_cases(entries: list[dict]) -> None:
    from app.models.use_case import UseCase, UseCaseSource, UseCaseStatus

    async with TestSessionLocal() as s:
        for e in entries:
            s.add(
                UseCase(
                    tenant_id=TEST_TENANT_ID,
                    title=f"{e['tool']} use",
                    tool=e["tool"],
                    department="Sales",
                    status=UseCaseStatus(e["status"].upper()),
                    source=UseCaseSource.FORM,
                    data_classes=json.dumps(e["data_classes"]),
                )
            )
        await s.commit()


async def _scim_groups(client: AsyncClient, groups: dict[str, list[str]]) -> None:
    r = await client.post("/api/v1/guide/scim-token", headers=_admin())
    assert r.status_code == 200, r.text
    scim = "/api/v1/scim/v2"
    h = {"Authorization": f"Bearer {r.json()['token']}", "Content-Type": "application/scim+json"}
    ids: dict[str, str] = {}
    for email in sorted({m for members in groups.values() for m in members}):
        r = await client.post(
            f"{scim}/Users",
            json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "userName": email},
            headers=h,
        )
        assert r.status_code == 201, r.text
        ids[email] = r.json()["id"]
    for name, members in groups.items():
        r = await client.post(
            f"{scim}/Groups",
            json={"displayName": name, "members": [{"value": ids[m]} for m in members]},
            headers=h,
        )
        assert r.status_code == 201, r.text


async def _send(client: AsyncClient, request: dict, headers: dict[str, str]):
    body = request.get("body")
    if body is not None and "period" in body:
        body = {**body, "period": _last_finished_month()}
    return await client.request(
        request["method"], "/" + request["path"], json=body, headers=headers
    )


def test_the_contract_covers_every_call_guide_makes() -> None:
    paths = {i["request"]["path"] for i in INTERACTIONS}
    assert paths == {
        "api/v1/guide/offer",
        "api/v1/guide/adoption",
        "api/v1/guide/approved-tools",
        "api/v1/guide/groups",
    }


@pytest.mark.parametrize("interaction", INTERACTIONS, ids=[i["name"] for i in INTERACTIONS])
async def test_atlas_answers_as_guide_reads(client: AsyncClient, interaction: dict) -> None:
    given = interaction["given"]
    if given.get("connected", True):
        r = await client.put(
            "/api/v1/guide/connection",
            json={"firebase_project_id": keys.PROJECT, "offered_kinds": given["offered_kinds"]},
            headers=_admin(),
        )
        assert r.status_code == 200, r.text
    if "use_cases" in given:
        await _use_cases(given["use_cases"])
    if "scim_groups" in given:
        await _scim_groups(client, given["scim_groups"])
    token = keys.token(overrides={"email": given["email"]}) if "email" in given else keys.token()
    headers = auth_header(token)
    if given.get("already_counted"):
        first = await _send(client, interaction["request"], headers)
        assert first.status_code == 200, first.text

    r = await _send(client, interaction["request"], headers)

    expected = interaction["response"]
    assert r.status_code == expected["status"], r.text
    if "body" in expected:
        assert holds(r.json(), expected["body"]), r.text
