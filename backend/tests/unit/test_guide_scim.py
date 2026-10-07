"""SCIM provisioning for Promptly Guide (promptly-guide #58), as Entra ID drives it.

What these defend: only the tenant's SCIM token gets in, and only to its own tenant;
the calls Entra's provisioning makes work as RFC 7644 says; nothing but userName,
externalId, active and group membership is kept; and Guide finds a person's groups by
their sign-in, and none once they are deprovisioned.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.auth.firebase_token import get_firebase_verifier
from app.auth.jwt import create_access_token
from app.main import app
from tests.conftest import TEST_TENANT_ID, auth_header
from tests.unit import firebase_keys as keys
from tests.unit.guide_db import guide_resolvers  # noqa: F401

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

OTHER_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-0000000000b4")
SCIM = "/api/v1/scim/v2"
PATCH_OP = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


@pytest.fixture(autouse=True)
def _local_google_keys():
    local = keys.verifier()
    app.dependency_overrides[get_firebase_verifier] = lambda: local
    yield
    app.dependency_overrides.pop(get_firebase_verifier, None)


def _admin(tenant_id: uuid.UUID = TEST_TENANT_ID) -> dict[str, str]:
    return auth_header(
        create_access_token(
            user_id=uuid.uuid4(),
            email="admin@example.com",
            roles=["TENANT_ADMIN"],
            tenant_id=tenant_id,
            org_id=tenant_id,
        )
    )


async def _scim_token(client: AsyncClient, tenant_id: uuid.UUID = TEST_TENANT_ID) -> dict[str, str]:
    r = await client.post("/api/v1/guide/scim-token", headers=_admin(tenant_id))
    assert r.status_code == 200, r.text
    assert r.json()["endpoint_path"] == SCIM
    return {"Authorization": f"Bearer {r.json()['token']}", "Content-Type": "application/scim+json"}


async def _user(client: AsyncClient, h: dict[str, str], user_name: str, **extra) -> str:
    body = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": user_name,
        **extra,
    }
    r = await client.post(f"{SCIM}/Users", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_only_the_tenants_scim_token_gets_in(client: AsyncClient) -> None:
    assert (await client.get(f"{SCIM}/Users")).status_code == 401
    bad = {"Authorization": "Bearer pss_not-a-token"}
    assert (await client.get(f"{SCIM}/Users", headers=bad)).status_code == 401
    jwt = _admin()
    assert (await client.get(f"{SCIM}/Users", headers=jwt)).status_code == 401


async def test_a_new_token_replaces_the_old_one(client: AsyncClient) -> None:
    first = await _scim_token(client)
    second = await _scim_token(client)
    assert (await client.get(f"{SCIM}/Users", headers=first)).status_code == 401
    assert (await client.get(f"{SCIM}/Users", headers=second)).status_code == 200
    await client.delete("/api/v1/guide/scim-token", headers=_admin())
    assert (await client.get(f"{SCIM}/Users", headers=second)).status_code == 401


async def test_a_user_keeps_only_what_group_enablement_needs(client: AsyncClient) -> None:
    h = await _scim_token(client)
    user_id = await _user(
        client,
        h,
        "Kari.Nordmann@nordlys.example",
        externalId="aad-1",
        active=True,
        name={"givenName": "Kari", "familyName": "Nordmann"},
        emails=[{"value": "kari@nordlys.example", "primary": True}],
        title="Controller",
    )
    r = await client.get(f"{SCIM}/Users/{user_id}", headers=h)
    assert r.headers["content-type"].startswith("application/scim+json")
    body = r.json()
    assert set(body) == {"schemas", "id", "userName", "active", "externalId", "meta"}
    assert body["userName"] == "Kari.Nordmann@nordlys.example"


async def test_entras_user_calls(client: AsyncClient) -> None:
    h = await _scim_token(client)
    # Entra looks the user up first, then creates.
    r = await client.get(
        f"{SCIM}/Users", params={"filter": 'userName eq "kari@nordlys.example"'}, headers=h
    )
    assert r.json()["totalResults"] == 0
    user_id = await _user(client, h, "kari@nordlys.example", externalId="aad-1")
    r = await client.get(
        f"{SCIM}/Users", params={"filter": 'userName eq "KARI@nordlys.example"'}, headers=h
    )
    assert [u["id"] for u in r.json()["Resources"]] == [user_id]
    dup = await client.post(f"{SCIM}/Users", json={"userName": "Kari@Nordlys.example"}, headers=h)
    assert dup.status_code == 409
    assert dup.json()["scimType"] == "uniqueness"
    # Disable, as Entra does, with a string value.
    patch = {
        "schemas": [PATCH_OP],
        "Operations": [{"op": "Replace", "path": "active", "value": "False"}],
    }
    r = await client.patch(f"{SCIM}/Users/{user_id}", json=patch, headers=h)
    assert r.json()["active"] is False
    # A path-less replace, and an attribute this store does not keep.
    patch = {
        "schemas": [PATCH_OP],
        "Operations": [
            {"op": "replace", "value": {"active": True, "externalId": "aad-2"}},
            {"op": "Replace", "path": "title", "value": "CFO"},
        ],
    }
    r = await client.patch(f"{SCIM}/Users/{user_id}", json=patch, headers=h)
    assert (r.json()["active"], r.json()["externalId"]) == (True, "aad-2")
    r = await client.put(
        f"{SCIM}/Users/{user_id}", json={"userName": "kari.n@nordlys.example"}, headers=h
    )
    assert r.json()["userName"] == "kari.n@nordlys.example"
    assert "externalId" not in r.json()
    assert (await client.delete(f"{SCIM}/Users/{user_id}", headers=h)).status_code == 204
    assert (await client.get(f"{SCIM}/Users/{user_id}", headers=h)).status_code == 404


async def test_a_filter_entra_does_not_send_is_refused_in_scims_words(client: AsyncClient) -> None:
    h = await _scim_token(client)
    r = await client.get(f"{SCIM}/Users", params={"filter": 'title co "x"'}, headers=h)
    assert r.status_code == 400
    assert r.json()["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
    assert r.json()["scimType"] == "invalidFilter"


async def test_entras_group_calls(client: AsyncClient) -> None:
    h = await _scim_token(client)
    kari = await _user(client, h, "kari@nordlys.example")
    ola = await _user(client, h, "ola@nordlys.example")
    r = await client.post(
        f"{SCIM}/Groups",
        json={"displayName": "Finance", "externalId": "g-1", "members": [{"value": kari}]},
        headers=h,
    )
    assert r.status_code == 201, r.text
    group_id = r.json()["id"]
    add = {
        "schemas": [PATCH_OP],
        "Operations": [{"op": "Add", "path": "members", "value": [{"value": ola}]}],
    }
    r = await client.patch(f"{SCIM}/Groups/{group_id}", json=add, headers=h)
    assert sorted(m["value"] for m in r.json()["members"]) == sorted([kari, ola])
    remove = {
        "schemas": [PATCH_OP],
        "Operations": [{"op": "Remove", "path": f'members[value eq "{kari}"]'}],
    }
    r = await client.patch(f"{SCIM}/Groups/{group_id}", json=remove, headers=h)
    assert [m["value"] for m in r.json()["members"]] == [ola]
    rename = {
        "schemas": [PATCH_OP],
        "Operations": [{"op": "Replace", "path": "displayName", "value": "Finance EU"}],
    }
    r = await client.patch(f"{SCIM}/Groups/{group_id}", json=rename, headers=h)
    assert r.json()["displayName"] == "Finance EU"
    r = await client.get(
        f"{SCIM}/Groups",
        params={"filter": 'displayName eq "Finance EU"', "excludedAttributes": "members"},
        headers=h,
    )
    assert [g["id"] for g in r.json()["Resources"]] == [group_id]
    assert "members" not in r.json()["Resources"][0]
    assert (await client.delete(f"{SCIM}/Groups/{group_id}", headers=h)).status_code == 204


async def test_one_tenants_token_never_reaches_anothers_users(client: AsyncClient) -> None:
    mine = await _scim_token(client)
    theirs = await _scim_token(client, OTHER_TENANT_ID)
    user_id = await _user(client, mine, "kari@nordlys.example")
    assert (await client.get(f"{SCIM}/Users/{user_id}", headers=theirs)).status_code == 404
    r = await client.get(f"{SCIM}/Users", headers=theirs)
    assert r.json()["totalResults"] == 0


async def _connect(client: AsyncClient) -> None:
    r = await client.put(
        "/api/v1/guide/connection",
        json={"firebase_project_id": keys.PROJECT, "offered_kinds": []},
        headers=_admin(),
    )
    assert r.status_code == 200, r.text


async def test_guide_gets_the_persons_groups_by_their_sign_in(client: AsyncClient) -> None:
    await _connect(client)
    h = await _scim_token(client)
    kari = await _user(client, h, "Kari@Nordlys.example")
    for name in ("Sales", "Finance"):
        await client.post(
            f"{SCIM}/Groups", json={"displayName": name, "members": [{"value": kari}]}, headers=h
        )
    await client.post(f"{SCIM}/Groups", json={"displayName": "Legal"}, headers=h)
    token = keys.token(overrides={"email": "kari@nordlys.example"})
    r = await client.get("/api/v1/guide/groups", headers=auth_header(token))
    assert r.status_code == 200, r.text
    assert r.json() == {"groups": ["Finance", "Sales"]}
    # Deprovisioned: no groups.
    off = {
        "schemas": [PATCH_OP],
        "Operations": [{"op": "Replace", "path": "active", "value": False}],
    }
    await client.patch(f"{SCIM}/Users/{kari}", json=off, headers=h)
    r = await client.get("/api/v1/guide/groups", headers=auth_header(token))
    assert r.json() == {"groups": []}


async def test_someone_not_provisioned_has_no_groups(client: AsyncClient) -> None:
    await _connect(client)
    token = keys.token(overrides={"email": "nobody@nordlys.example"})
    r = await client.get("/api/v1/guide/groups", headers=auth_header(token))
    assert r.json() == {"groups": []}
