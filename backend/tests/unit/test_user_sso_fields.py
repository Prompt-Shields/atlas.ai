"""SSO foundation (PRO-54): a User can be SSO-only (no password) and linked
to an Entra identity via a unique `entra_object_id`."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.user import User
from tests.conftest import TestSessionLocal


async def test_user_persists_without_password_with_entra_oid() -> None:
    """An SSO-only user has no password but is linked by Entra object id."""
    user_id = uuid.uuid4()
    oid = f"entra-{uuid.uuid4()}"

    async with TestSessionLocal() as session:
        session.add(
            User(
                id=user_id,
                email="sso-user@test.local",
                hashed_password=None,
                full_name="SSO Only User",
                entra_object_id=oid,
                is_active=True,
                is_email_verified=True,
                is_test_data=True,
            )
        )
        await session.commit()

    async with TestSessionLocal() as session:
        fetched = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
        assert fetched.hashed_password is None
        assert fetched.entra_object_id == oid


async def test_entra_object_id_is_unique() -> None:
    """Two users cannot share the same Entra object id."""
    oid = f"entra-{uuid.uuid4()}"

    async with TestSessionLocal() as session:
        session.add_all(
            [
                User(
                    id=uuid.uuid4(),
                    email="dup-a@test.local",
                    hashed_password=None,
                    full_name="Dup A",
                    entra_object_id=oid,
                    is_test_data=True,
                ),
                User(
                    id=uuid.uuid4(),
                    email="dup-b@test.local",
                    hashed_password=None,
                    full_name="Dup B",
                    entra_object_id=oid,
                    is_test_data=True,
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            await session.commit()


def test_sso_column_metadata() -> None:
    """Schema contract: password optional, entra_object_id present/unique/indexed."""
    columns = User.__table__.columns
    assert columns["hashed_password"].nullable is True
    entra = columns["entra_object_id"]
    assert entra.nullable is True
    assert entra.unique is True
    assert entra.index is True
