"""DirectoryGroupMembership model + Graph member helpers (PRO-56).

Adds the group-membership join the DirectoryGroup docstring flagged as a
follow-up, so the admin portal (PRO-58) can list a team's members.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.directory import (
    DirectoryGroup,
    DirectoryGroupMembership,
    DirectoryUser,
)
from app.services.microsoft_graph import normalise_member
from tests.conftest import TestSessionLocal

TENANT = uuid.uuid4()
INTEGRATION = uuid.uuid4()


def test_membership_table_and_constraint() -> None:
    cols = {c.name for c in DirectoryGroupMembership.__table__.columns}
    assert DirectoryGroupMembership.__tablename__ == "directory_group_memberships"
    assert DirectoryGroupMembership.__table__.schema == "grc"
    assert cols >= {"tenant_id", "integration_id", "group_id", "user_id", "last_synced_at"}
    uniques = {
        tuple(sorted(c.name for c in con.columns))
        for con in DirectoryGroupMembership.__table__.constraints
        if con.__class__.__name__ == "UniqueConstraint"
    }
    assert ("group_id", "user_id") in uniques


def test_normalise_member_extracts_user_id() -> None:
    assert normalise_member({"id": "  abc-123 ", "displayName": "X"}) == {
        "external_user_id": "abc-123"
    }
    assert normalise_member({})["external_user_id"] == ""


async def _seed_group_and_user() -> tuple[uuid.UUID, uuid.UUID]:
    now = datetime.now(UTC)
    gid, uid = uuid.uuid4(), uuid.uuid4()
    async with TestSessionLocal() as session:
        session.add(
            DirectoryGroup(
                id=gid,
                tenant_id=TENANT,
                integration_id=INTEGRATION,
                external_group_id="grp-1",
                display_name="Team One",
                last_synced_at=now,
                is_test_data=True,
            )
        )
        session.add(
            DirectoryUser(
                id=uid,
                tenant_id=TENANT,
                integration_id=INTEGRATION,
                external_user_id="usr-1",
                display_name="User One",
                last_synced_at=now,
                is_test_data=True,
            )
        )
        await session.commit()
    return gid, uid


async def test_membership_persists_and_is_unique() -> None:
    gid, uid = await _seed_group_and_user()
    now = datetime.now(UTC)
    async with TestSessionLocal() as session:
        session.add(
            DirectoryGroupMembership(
                id=uuid.uuid4(),
                tenant_id=TENANT,
                integration_id=INTEGRATION,
                group_id=gid,
                user_id=uid,
                last_synced_at=now,
                is_test_data=True,
            )
        )
        await session.commit()

    async with TestSessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(DirectoryGroupMembership).where(DirectoryGroupMembership.group_id == gid)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1 and rows[0].user_id == uid

    # Same (group, user) again -> unique violation.
    async with TestSessionLocal() as session:
        session.add(
            DirectoryGroupMembership(
                id=uuid.uuid4(),
                tenant_id=TENANT,
                integration_id=INTEGRATION,
                group_id=gid,
                user_id=uid,
                last_synced_at=now,
                is_test_data=True,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_replace_group_memberships_upserts_and_prunes() -> None:
    """_replace_group_memberships makes the set exact: add, keep, prune."""
    import types

    from app.services.microsoft_sync import _replace_group_memberships

    now = datetime.now(UTC)
    gid = uuid.uuid4()
    u1, u2 = uuid.uuid4(), uuid.uuid4()
    async with TestSessionLocal() as session:
        session.add(
            DirectoryGroup(
                id=gid,
                tenant_id=TENANT,
                integration_id=INTEGRATION,
                external_group_id="grp-x",
                display_name="X",
                last_synced_at=now,
                is_test_data=True,
            )
        )
        for uid_, ext in ((u1, "u1"), (u2, "u2")):
            session.add(
                DirectoryUser(
                    id=uid_,
                    tenant_id=TENANT,
                    integration_id=INTEGRATION,
                    external_user_id=ext,
                    display_name=ext,
                    last_synced_at=now,
                    is_test_data=True,
                )
            )
        await session.commit()

    integration = types.SimpleNamespace(tenant_id=TENANT, id=INTEGRATION)

    async def members_for(group_id: uuid.UUID) -> set[uuid.UUID]:
        async with TestSessionLocal() as s:
            rows = (
                (
                    await s.execute(
                        select(DirectoryGroupMembership.user_id).where(
                            DirectoryGroupMembership.group_id == group_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            return set(rows)

    # First sync: {u1}
    async with TestSessionLocal() as session:
        n = await _replace_group_memberships(session, integration, gid, {u1}, now)
        await session.commit()
    assert n == 1 and await members_for(gid) == {u1}

    # Add u2 (idempotent re-add of u1)
    async with TestSessionLocal() as session:
        await _replace_group_memberships(session, integration, gid, {u1, u2}, now)
        await session.commit()
    assert await members_for(gid) == {u1, u2}

    # u1 leaves the group -> pruned
    async with TestSessionLocal() as session:
        await _replace_group_memberships(session, integration, gid, {u2}, now)
        await session.commit()
    assert await members_for(gid) == {u2}
