"""The audit log of SCIM provisioning (promptly-guide #58, E10).

What these defend: every change the identity provider makes, and every SCIM token an
admin makes or revokes, leaves one audit entry in the same transaction; a re-sent
PATCH that changes nothing, and a refused write, leave none; entries carry only
directory data (names, external ids, `active`, member ids), never the token; and each
entry is filed under the tenant whose token made the change.

The request bodies are the ones Microsoft documents for Entra ID's provisioning
service ("Tutorial: Develop and plan provisioning for a SCIM endpoint", request and
response examples), down to `"value": "False"` as a string and `"$ref": null`.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.auth.jwt import create_access_token
from app.models.audit_log import AuditLog
from tests.conftest import TEST_TENANT_ID, TestSessionLocal, auth_header
from tests.unit.guide_db import guide_resolvers  # noqa: F401

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

OTHER_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-0000000000b5")
SCIM = "/api/v1/scim/v2"
PATCH_OP = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
ENTERPRISE = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
ADSCIM_GROUP = "http://schemas.microsoft.com/2006/11/ResourceManagement/ADSCIM/2.0/Group"
ADMIN_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a7")

# Everything an entry's details may hold: directory data, never usage or secrets.
DIRECTORY_KEYS = {
    "userName",
    "externalId",
    "active",
    "displayName",
    "membersAdded",
    "membersRemoved",
    "groupsLeft",
    "membersLeft",
    "replaced",
}


def _admin(tenant_id: uuid.UUID = TEST_TENANT_ID) -> dict[str, str]:
    return auth_header(
        create_access_token(
            user_id=ADMIN_ID,
            email="admin@example.com",
            roles=["TENANT_ADMIN"],
            tenant_id=tenant_id,
            org_id=tenant_id,
        )
    )


async def _scim_token(client: AsyncClient, tenant_id: uuid.UUID = TEST_TENANT_ID) -> str:
    r = await client.post("/api/v1/guide/scim-token", headers=_admin(tenant_id))
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/scim+json"}


async def _entries(tenant_id: uuid.UUID = TEST_TENANT_ID) -> list[AuditLog]:
    async with TestSessionLocal() as db:
        rows = await db.execute(
            select(AuditLog).where(
                AuditLog.tenant_id == tenant_id, AuditLog.event_type.like("scim.%")
            )
        )
        return list(rows.scalars())


def _details(entry: AuditLog) -> dict[str, Any]:
    return json.loads(entry.details) if entry.details else {}


async def _seen(tenant_id: uuid.UUID = TEST_TENANT_ID) -> set[uuid.UUID]:
    """The entries so far. Timestamps tie within a second on SQLite, so new entries are
    told apart by id, not by order."""
    return {e.id for e in await _entries(tenant_id)}


async def _new_entries(seen: set[uuid.UUID]) -> list[AuditLog]:
    """The entries since `seen`, and adds them to it."""
    new = [e for e in await _entries() if e.id not in seen]
    seen.update(e.id for e in new)
    return new


def _entra_user(user_name: str, external_id: str) -> dict[str, Any]:
    return {
        "schemas": [USER_SCHEMA, ENTERPRISE],
        "externalId": external_id,
        "userName": user_name,
        "active": True,
        "emails": [{"primary": True, "type": "work", "value": user_name}],
        "meta": {"resourceType": "User"},
        "name": {"formatted": "Kari Nordmann", "familyName": "Nordmann", "givenName": "Kari"},
        "roles": [],
    }


def _patch(*operations: dict[str, Any]) -> dict[str, Any]:
    return {"schemas": [PATCH_OP], "Operations": list(operations)}


async def test_a_users_lifecycle_is_audited_once_per_change(client: AsyncClient) -> None:
    h = _bearer(await _scim_token(client))
    seen = await _seen()

    r = await client.post(
        f"{SCIM}/Users", json=_entra_user("kari@nordlys.example", "aad-kari"), headers=h
    )
    assert r.status_code == 201, r.text
    user_id = r.json()["id"]
    [created] = await _new_entries(seen)
    assert (created.event_type, created.action) == ("scim.user.created", "create")
    assert (created.resource_type, created.resource_id) == ("scim_user", user_id)
    assert created.actor_id is None and created.actor_email is None
    assert _details(created) == {
        "userName": "kari@nordlys.example",
        "externalId": "aad-kari",
        "active": True,
    }

    # Disable, as Entra sends it: the value is the string "False".
    disable = _patch({"op": "Replace", "path": "active", "value": "False"})
    assert (await client.patch(f"{SCIM}/Users/{user_id}", json=disable, headers=h)).json()[
        "active"
    ] is False
    [disabled] = await _new_entries(seen)
    assert (disabled.event_type, disabled.action) == ("scim.user.updated", "update")
    assert _details(disabled) == {"active": {"from": True, "to": False}}

    # Entra re-sends the same state on its next cycle: nothing changed, nothing logged.
    assert (await client.patch(f"{SCIM}/Users/{user_id}", json=disable, headers=h)).is_success
    assert await _new_entries(seen) == []

    rename = _patch(
        {"op": "Replace", "path": "userName", "value": "kari.n@nordlys.example"},
        {"op": "Replace", "path": "displayName", "value": "Kari N"},
    )
    assert (await client.patch(f"{SCIM}/Users/{user_id}", json=rename, headers=h)).is_success
    [renamed] = await _new_entries(seen)
    assert _details(renamed) == {
        "userName": {"from": "kari@nordlys.example", "to": "kari.n@nordlys.example"}
    }

    # PUT replaces the whole user: it re-enables and drops the external id.
    r = await client.put(
        f"{SCIM}/Users/{user_id}", json={"userName": "kari.n@nordlys.example"}, headers=h
    )
    assert r.status_code == 200, r.text
    [replaced] = await _new_entries(seen)
    assert _details(replaced) == {
        "externalId": {"from": "aad-kari", "to": None},
        "active": {"from": False, "to": True},
    }

    assert (await client.delete(f"{SCIM}/Users/{user_id}", headers=h)).status_code == 204
    [deleted] = await _new_entries(seen)
    assert (deleted.event_type, deleted.action) == ("scim.user.deleted", "delete")
    assert deleted.resource_id == user_id
    assert _details(deleted) == {
        "userName": "kari.n@nordlys.example",
        "externalId": None,
        "active": True,
        "groupsLeft": 0,
    }


async def test_a_groups_membership_changes_are_audited(client: AsyncClient) -> None:
    h = _bearer(await _scim_token(client))
    kari = (
        await client.post(f"{SCIM}/Users", json=_entra_user("kari@n.example", "a1"), headers=h)
    ).json()["id"]
    ola = (
        await client.post(f"{SCIM}/Users", json=_entra_user("ola@n.example", "a2"), headers=h)
    ).json()["id"]
    seen = await _seen()

    # Entra creates the group empty, then PATCHes members in.
    r = await client.post(
        f"{SCIM}/Groups",
        json={
            "schemas": [GROUP_SCHEMA, ADSCIM_GROUP],
            "externalId": "8aa1a0c0-c4c3-4bc0-b4a5-2ef676900159",
            "displayName": "Finance",
            "meta": {"resourceType": "Group"},
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    group_id = r.json()["id"]
    [created] = await _new_entries(seen)
    assert (created.event_type, created.resource_type) == ("scim.group.created", "scim_group")
    assert _details(created) == {
        "displayName": "Finance",
        "externalId": "8aa1a0c0-c4c3-4bc0-b4a5-2ef676900159",
    }

    add = _patch(
        {
            "op": "Add",
            "path": "members",
            "value": [{"$ref": None, "value": kari}, {"$ref": None, "value": ola}],
        }
    )
    assert (await client.patch(f"{SCIM}/Groups/{group_id}", json=add, headers=h)).is_success
    [added] = await _new_entries(seen)
    assert (added.event_type, added.action) == ("scim.group.updated", "update")
    assert _details(added) == {"membersAdded": {"count": 2, "ids": sorted([kari, ola])}}

    # Adding someone already in the group changes nothing.
    again = _patch({"op": "Add", "path": "members", "value": [{"$ref": None, "value": kari}]})
    assert (await client.patch(f"{SCIM}/Groups/{group_id}", json=again, headers=h)).is_success
    assert await _new_entries(seen) == []

    remove = _patch({"op": "Remove", "path": "members", "value": [{"$ref": None, "value": kari}]})
    rename = {"op": "Replace", "path": "displayName", "value": "Finance EU"}
    remove["Operations"].append(rename)
    assert (await client.patch(f"{SCIM}/Groups/{group_id}", json=remove, headers=h)).is_success
    [changed] = await _new_entries(seen)
    assert _details(changed) == {
        "displayName": {"from": "Finance", "to": "Finance EU"},
        "membersRemoved": {"count": 1, "ids": [kari]},
    }

    # Deleting a user takes them out of their groups; the entry says how many.
    assert (await client.delete(f"{SCIM}/Users/{ola}", headers=h)).status_code == 204
    [user_gone] = await _new_entries(seen)
    assert _details(user_gone)["groupsLeft"] == 1

    assert (await client.delete(f"{SCIM}/Groups/{group_id}", headers=h)).status_code == 204
    [deleted] = await _new_entries(seen)
    assert (deleted.event_type, deleted.resource_id) == ("scim.group.deleted", group_id)
    assert _details(deleted) == {
        "displayName": "Finance EU",
        "externalId": "8aa1a0c0-c4c3-4bc0-b4a5-2ef676900159",
        "membersLeft": 0,
    }


async def test_a_refused_write_leaves_no_entry(client: AsyncClient) -> None:
    h = _bearer(await _scim_token(client))
    body = _entra_user("kari@nordlys.example", "aad-kari")
    assert (await client.post(f"{SCIM}/Users", json=body, headers=h)).status_code == 201
    seen = await _seen()
    dup = await client.post(f"{SCIM}/Users", json=body, headers=h)
    assert dup.status_code == 409
    bad = _patch({"op": "Replace", "path": "active", "value": "maybe"})
    user_id = (
        await client.get(
            f"{SCIM}/Users", params={"filter": 'userName eq "kari@nordlys.example"'}, headers=h
        )
    ).json()["Resources"][0]["id"]
    assert (await client.patch(f"{SCIM}/Users/{user_id}", json=bad, headers=h)).status_code == 400
    assert await _new_entries(seen) == []


async def test_scim_tokens_are_audited_without_the_token(client: AsyncClient) -> None:
    seen: set[uuid.UUID] = set()
    first = await _scim_token(client)
    [made] = await _new_entries(seen)
    second = await _scim_token(client)
    [remade] = await _new_entries(seen)
    assert (await client.delete("/api/v1/guide/scim-token", headers=_admin())).status_code == 204
    [revoked] = await _new_entries(seen)
    # Revoking when there is no token is not a change.
    assert (await client.delete("/api/v1/guide/scim-token", headers=_admin())).status_code == 204
    assert await _new_entries(seen) == []

    entries = [made, remade, revoked]
    assert [(e.event_type, e.action, _details(e)) for e in entries] == [
        ("scim.token.created", "create", {"replaced": False}),
        ("scim.token.created", "create", {"replaced": True}),
        ("scim.token.revoked", "delete", {}),
    ]
    for entry in entries:
        assert (entry.actor_id, entry.actor_email) == (ADMIN_ID, "admin@example.com")
        assert entry.resource_type == "scim_token"
        for secret in (first, second, first[4:], second[4:]):
            assert secret not in (entry.details or "")


async def test_entries_go_to_the_calling_tenant_and_hold_only_directory_data(
    client: AsyncClient,
) -> None:
    mine = _bearer(await _scim_token(client))
    theirs = _bearer(await _scim_token(client, OTHER_TENANT_ID))
    user_id = (
        await client.post(f"{SCIM}/Users", json=_entra_user("kari@n.example", "a1"), headers=mine)
    ).json()["id"]
    group = await client.post(
        f"{SCIM}/Groups",
        json={"displayName": "Finance", "members": [{"value": user_id}]},
        headers=mine,
    )
    assert group.status_code == 201, group.text
    # The other tenant's token cannot touch it, and leaves no entry trying.
    disable = _patch({"op": "Replace", "path": "active", "value": False})
    assert (
        await client.patch(f"{SCIM}/Users/{user_id}", json=disable, headers=theirs)
    ).status_code == 404

    other = await _entries(OTHER_TENANT_ID)
    assert [e.event_type for e in other] == ["scim.token.created"]
    entries = {e.event_type: e for e in await _entries()}
    assert sorted(entries) == ["scim.group.created", "scim.token.created", "scim.user.created"]
    assert _details(entries["scim.group.created"])["membersAdded"] == {
        "count": 1,
        "ids": [user_id],
    }
    for entry in entries.values():
        assert set(_details(entry)) <= DIRECTORY_KEYS
