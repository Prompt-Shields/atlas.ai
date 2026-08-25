"""DirectoryUser manager/report relations (PRO-56).

Stores each user's manager (Entra `manager` id). Direct reports are derivable
(users whose manager id == this user's external id), so no separate table.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.models.directory import DirectoryUser
from app.services.microsoft_graph import normalise_user
from tests.conftest import TestSessionLocal

TENANT = uuid.uuid4()
INTEGRATION = uuid.uuid4()


def test_normalise_user_extracts_manager_id() -> None:
    raw = {"id": "u1", "displayName": "U1", "manager": {"id": "mgr-9", "x": 1}}
    assert normalise_user(raw)["manager_external_user_id"] == "mgr-9"


def test_normalise_user_no_manager_is_none() -> None:
    assert normalise_user({"id": "u2"})["manager_external_user_id"] is None


def test_directory_user_has_manager_column() -> None:
    col = DirectoryUser.__table__.columns["manager_external_user_id"]
    assert col.nullable is True


async def test_direct_reports_are_queryable() -> None:
    """Reports = users whose manager id is the manager's external id."""
    now = datetime.now(UTC)
    async with TestSessionLocal() as session:
        session.add(
            DirectoryUser(
                id=uuid.uuid4(),
                tenant_id=TENANT,
                integration_id=INTEGRATION,
                external_user_id="boss",
                display_name="Boss",
                manager_external_user_id=None,
                last_synced_at=now,
                is_test_data=True,
            )
        )
        for ext in ("rep-a", "rep-b"):
            session.add(
                DirectoryUser(
                    id=uuid.uuid4(),
                    tenant_id=TENANT,
                    integration_id=INTEGRATION,
                    external_user_id=ext,
                    display_name=ext,
                    manager_external_user_id="boss",
                    last_synced_at=now,
                    is_test_data=True,
                )
            )
        await session.commit()

    async with TestSessionLocal() as session:
        reports = (
            (
                await session.execute(
                    select(DirectoryUser.external_user_id).where(
                        DirectoryUser.tenant_id == TENANT,
                        DirectoryUser.manager_external_user_id == "boss",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert set(reports) == {"rep-a", "rep-b"}
