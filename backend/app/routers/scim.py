"""SCIM 2.0 for Promptly Guide — `/api/v1/scim/v2` (promptly-guide #58, E10).

The customer's identity provider (Entra ID's provisioning service) pushes users and
groups here, authenticated by the tenant's SCIM token (`app/auth/scim_token.py`).
What is stored is in `app/models/guide_scim.py`: user names, external ids, active,
and group membership. Every other attribute an identity provider sends is accepted
and discarded.

The subset Entra's provisioning uses (RFC 7643/7644):

  GET    /Users?filter=userName eq "x"   (also externalId eq)   list, filtered
  POST   /Users                                                  create (409 if taken)
  GET    /Users/{id}
  PUT    /Users/{id}                                             replace
  PATCH  /Users/{id}                                             Replace/Add on
                                                                 active, userName, externalId
  DELETE /Users/{id}
  GET    /Groups?filter=displayName eq "x"  (also externalId eq)
  POST   /Groups
  GET    /Groups/{id}                    (?excludedAttributes=members honoured)
  PATCH  /Groups/{id}                    add/remove members, replace displayName
  DELETE /Groups/{id}
  GET    /ServiceProviderConfig

Errors use SCIM's own shape (`urn:ietf:params:scim:api:messages:2.0:Error`).

Every change is written to the audit log (`audit.audit_logs`) in the same transaction:
`scim.user.created|updated|deleted` and `scim.group.created|updated|deleted`, with no
actor (the identity provider made it) and only directory data in the details: names,
external ids, `active`, and which members came and went. A PATCH or PUT that changes
nothing (Entra re-sends the same values on every cycle) writes no entry.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.scim_token import require_scim_tenant
from app.database import DB_SESSION
from app.models.guide_scim import GuideScimGroup, GuideScimMember, GuideScimUser
from app.services.audit import log_audit_event

router = APIRouter(prefix="/scim/v2", tags=["SCIM"])

USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
MEDIA_TYPE = "application/scim+json"
MAX_PAGE = 200
# Member ids listed in one audit entry; a larger change keeps its counts.
AUDIT_MEMBER_IDS = 100
_ACTIONS = {"created": "create", "updated": "update", "deleted": "delete"}

_FILTER_RE = re.compile(r'^\s*(\w+)\s+eq\s+"((?:[^"\\]|\\.)*)"\s*$', re.IGNORECASE)
_MEMBER_PATH_RE = re.compile(r'^members\[value\s+eq\s+"([^"]+)"\]$', re.IGNORECASE)


class ScimError(Exception):
    def __init__(self, status: int, detail: str, scim_type: str | None = None) -> None:
        self.status = status
        self.detail = detail
        self.scim_type = scim_type
        super().__init__(detail)


def _scim(body: dict[str, Any], status: int = 200) -> JSONResponse:
    return JSONResponse(body, status_code=status, media_type=MEDIA_TYPE)


def _error(error: ScimError) -> JSONResponse:
    body: dict[str, Any] = {
        "schemas": [ERROR_SCHEMA],
        "status": str(error.status),
        "detail": error.detail,
    }
    if error.scim_type:
        body["scimType"] = error.scim_type
    return _scim(body, error.status)


# How each unique name's violation reads: Postgres names the constraint, SQLite the
# columns.
_UNIQUE_NAMES = {
    "userName": ("uq_guide_scim_users_name", "guide_scim_users.user_name_key"),
    "displayName": ("uq_guide_scim_groups_name", "guide_scim_groups.display_name"),
}


async def _write(db: AsyncSession, attribute: str, *, commit: bool = True) -> None:
    """Commits (or flushes). The name check before it cannot see a request running
    at the same time, so two creates of one name can both pass it; the database's
    unique constraint then refuses the second, and that is the same 409 a
    sequential duplicate gets (RFC 7644 §3.3), not a 500."""
    try:
        await (db.commit() if commit else db.flush())
    except IntegrityError as error:
        await db.rollback()
        if any(marker in str(error.orig) for marker in _UNIQUE_NAMES[attribute]):
            raise ScimError(409, f"{attribute} is already provisioned", "uniqueness") from error
        raise


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise ScimError(404, "Resource not found")


def _filter(text: str | None, allowed: dict[str, Any]) -> Any | None:
    """The one `attr eq "value"` filter Entra sends, as a SQL clause; None for no filter."""
    if not text:
        return None
    match = _FILTER_RE.match(text)
    if not match or match.group(1).lower() not in allowed:
        raise ScimError(
            400, 'Only `<attribute> eq "<value>"` filters are supported', "invalidFilter"
        )
    value = match.group(2).replace('\\"', '"')
    return allowed[match.group(1).lower()](value)


def _bool(value: Any) -> bool:
    # Entra sends "True"/"False" as strings in PATCH values.
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise ScimError(400, "active must be true or false", "invalidValue")


def _text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ScimError(400, f"{field} must be text of at most {limit} characters", "invalidValue")
    return value.strip()


async def _audit(
    db: AsyncSession,
    request: Request,
    tenant_id: uuid.UUID,
    event_type: str,
    resource_id: uuid.UUID,
    details: dict[str, Any],
) -> None:
    """One audit entry for a change the identity provider made. Flushed, not
    committed: it lands with the change or not at all. `event_type` is
    `scim.<user|group>.<created|updated|deleted>`."""
    _, kind, done = event_type.split(".")
    await log_audit_event(
        db,
        event_type=event_type,
        action=_ACTIONS[done],
        tenant_id=tenant_id,
        resource_type=f"scim_{kind}",
        resource_id=str(resource_id),
        details=details,
        correlation_id=getattr(request.state, "correlation_id", None),
    )


def _user_state(user: GuideScimUser) -> dict[str, Any]:
    return {"userName": user.user_name, "externalId": user.external_id, "active": user.active}


def _changes(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """{attribute: {"from": old, "to": new}} for each attribute that changed."""
    return {k: {"from": before[k], "to": after[k]} for k in after if before[k] != after[k]}


async def _member_ids(db: AsyncSession, group_id: uuid.UUID) -> set[uuid.UUID]:
    rows = await db.execute(
        select(GuideScimMember.user_id).where(GuideScimMember.group_id == group_id)
    )
    return set(rows.scalars())


def _member_details(added: set[uuid.UUID], removed: set[uuid.UUID]) -> dict[str, Any]:
    details: dict[str, Any] = {}
    for key, ids in (("membersAdded", added), ("membersRemoved", removed)):
        if ids:
            listed = sorted(str(i) for i in ids)
            details[key] = {"count": len(ids), "ids": listed[:AUDIT_MEMBER_IDS]}
    return details


# ── Users ─────────────────────────────────────────────────────────────────────


def _user_json(user: GuideScimUser) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schemas": [USER_SCHEMA],
        "id": str(user.id),
        "userName": user.user_name,
        "active": user.active,
        "meta": {"resourceType": "User"},
    }
    if user.external_id:
        body["externalId"] = user.external_id
    return body


async def _user(db: AsyncSession, tenant_id: uuid.UUID, user_id: str) -> GuideScimUser:
    user = (
        await db.execute(
            select(GuideScimUser).where(
                GuideScimUser.tenant_id == tenant_id, GuideScimUser.id == _uuid(user_id)
            )
        )
    ).scalar_one_or_none()
    if user is None:
        raise ScimError(404, "User not found")
    return user


async def _name_taken(
    db: AsyncSession, tenant_id: uuid.UUID, key: str, other_than: uuid.UUID | None = None
) -> bool:
    query = select(GuideScimUser.id).where(
        GuideScimUser.tenant_id == tenant_id, GuideScimUser.user_name_key == key
    )
    if other_than is not None:
        query = query.where(GuideScimUser.id != other_than)
    # Not flushed first: a rename to a taken name would hit the unique constraint
    # in the flush and never reach the check.
    with db.no_autoflush:
        return (await db.execute(query)).first() is not None


def _apply_user(user: GuideScimUser, values: dict[str, Any]) -> None:
    """Sets the attributes this store keeps from a SCIM user body; ignores the rest."""
    lowered = {k.lower(): v for k, v in values.items()}
    if "username" in lowered:
        user.user_name = _text(lowered["username"], "userName", 320)
        user.user_name_key = user.user_name.lower()
    if "externalid" in lowered:
        user.external_id = (
            None
            if lowered["externalid"] is None
            else _text(lowered["externalid"], "externalId", 255)
        )
    if "active" in lowered:
        user.active = _bool(lowered["active"])


async def _save_user(
    db: AsyncSession,
    request: Request,
    tenant_id: uuid.UUID,
    user: GuideScimUser,
    before: dict[str, Any],
) -> None:
    """Checks the name, then commits a PUT or PATCH with its audit entry (none when
    nothing changed)."""
    if await _name_taken(db, tenant_id, user.user_name_key, other_than=user.id):
        raise ScimError(409, "userName is already provisioned", "uniqueness")
    changed = _changes(before, _user_state(user))
    if changed:
        await _write(db, "userName", commit=False)
        await _audit(db, request, tenant_id, "scim.user.updated", user.id, changed)
    await _write(db, "userName")


@router.get("/Users")
async def list_users(
    tenant_id: uuid.UUID = Depends(require_scim_tenant),
    db: AsyncSession = DB_SESSION,
    filter: str | None = Query(None),  # noqa: A002 — SCIM's parameter name
    startIndex: int = Query(1, ge=1),  # noqa: N803
    count: int = Query(100, ge=0),
) -> Response:
    try:
        clause = _filter(
            filter,
            {
                "username": lambda v: GuideScimUser.user_name_key == v.lower(),
                "externalid": lambda v: GuideScimUser.external_id == v,
            },
        )
    except ScimError as error:
        return _error(error)
    base = select(GuideScimUser).where(GuideScimUser.tenant_id == tenant_id)
    if clause is not None:
        base = base.where(clause)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        (
            await db.execute(
                base.order_by(GuideScimUser.user_name_key)
                .offset(startIndex - 1)
                .limit(min(count, MAX_PAGE))
            )
        )
        .scalars()
        .all()
    )
    return _scim(
        {
            "schemas": [LIST_SCHEMA],
            "totalResults": total,
            "startIndex": startIndex,
            "itemsPerPage": len(rows),
            "Resources": [_user_json(u) for u in rows],
        }
    )


@router.post("/Users")
async def create_user(
    request: Request,
    payload: dict[str, Any] = Body(...),
    tenant_id: uuid.UUID = Depends(require_scim_tenant),
    db: AsyncSession = DB_SESSION,
) -> Response:
    try:
        user = GuideScimUser(tenant_id=tenant_id, active=True)
        if "userName" not in payload:
            raise ScimError(400, "userName is required", "invalidValue")
        _apply_user(user, payload)
        if await _name_taken(db, tenant_id, user.user_name_key):
            raise ScimError(409, "userName is already provisioned", "uniqueness")
        db.add(user)
        await _write(db, "userName", commit=False)
        await _audit(db, request, tenant_id, "scim.user.created", user.id, _user_state(user))
        await db.commit()
    except ScimError as error:
        return _error(error)
    await db.refresh(user)
    return _scim(_user_json(user), 201)


@router.get("/Users/{user_id}")
async def get_user(
    user_id: str,
    tenant_id: uuid.UUID = Depends(require_scim_tenant),
    db: AsyncSession = DB_SESSION,
) -> Response:
    try:
        return _scim(_user_json(await _user(db, tenant_id, user_id)))
    except ScimError as error:
        return _error(error)


@router.put("/Users/{user_id}")
async def replace_user(
    user_id: str,
    request: Request,
    payload: dict[str, Any] = Body(...),
    tenant_id: uuid.UUID = Depends(require_scim_tenant),
    db: AsyncSession = DB_SESSION,
) -> Response:
    try:
        user = await _user(db, tenant_id, user_id)
        before = _user_state(user)
        if "userName" not in payload:
            raise ScimError(400, "userName is required", "invalidValue")
        user.external_id = None
        user.active = True
        _apply_user(user, payload)
        await _save_user(db, request, tenant_id, user, before)
    except ScimError as error:
        await db.rollback()
        return _error(error)
    await db.refresh(user)
    return _scim(_user_json(user))


@router.patch("/Users/{user_id}")
async def patch_user(
    user_id: str,
    request: Request,
    payload: dict[str, Any] = Body(...),
    tenant_id: uuid.UUID = Depends(require_scim_tenant),
    db: AsyncSession = DB_SESSION,
) -> Response:
    try:
        user = await _user(db, tenant_id, user_id)
        before = _user_state(user)
        for op in _operations(payload):
            kind = str(op.get("op", "")).lower()
            if kind not in ("add", "replace"):
                # Removing an attribute this store does not keep changes nothing here.
                continue
            path = op.get("path")
            value = op.get("value")
            if path:
                _apply_user(user, {str(path): value})
            elif isinstance(value, dict):
                _apply_user(user, value)
        await _save_user(db, request, tenant_id, user, before)
    except ScimError as error:
        await db.rollback()
        return _error(error)
    await db.refresh(user)
    return _scim(_user_json(user))


@router.delete("/Users/{user_id}")
async def delete_user(
    user_id: str,
    request: Request,
    tenant_id: uuid.UUID = Depends(require_scim_tenant),
    db: AsyncSession = DB_SESSION,
) -> Response:
    try:
        user = await _user(db, tenant_id, user_id)
    except ScimError as error:
        return _error(error)
    groups = await db.execute(select(func.count()).where(GuideScimMember.user_id == user.id))
    details = {**_user_state(user), "groupsLeft": groups.scalar_one()}
    await db.execute(delete(GuideScimMember).where(GuideScimMember.user_id == user.id))
    await db.delete(user)
    await _audit(db, request, tenant_id, "scim.user.deleted", user.id, details)
    await db.commit()
    return Response(status_code=204)


# ── Groups ────────────────────────────────────────────────────────────────────


async def _group(db: AsyncSession, tenant_id: uuid.UUID, group_id: str) -> GuideScimGroup:
    group = (
        await db.execute(
            select(GuideScimGroup).where(
                GuideScimGroup.tenant_id == tenant_id, GuideScimGroup.id == _uuid(group_id)
            )
        )
    ).scalar_one_or_none()
    if group is None:
        raise ScimError(404, "Group not found")
    return group


async def _group_json(
    db: AsyncSession, group: GuideScimGroup, with_members: bool = True
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schemas": [GROUP_SCHEMA],
        "id": str(group.id),
        "displayName": group.display_name,
        "meta": {"resourceType": "Group"},
    }
    if group.external_id:
        body["externalId"] = group.external_id
    if with_members:
        members = (
            (
                await db.execute(
                    select(GuideScimMember.user_id).where(GuideScimMember.group_id == group.id)
                )
            )
            .scalars()
            .all()
        )
        body["members"] = [{"value": str(m)} for m in sorted(members, key=str)]
    return body


async def _add_members(
    db: AsyncSession, tenant_id: uuid.UUID, group: GuideScimGroup, values: Any
) -> None:
    if not isinstance(values, list):
        raise ScimError(400, "members must be a list", "invalidValue")
    for item in values:
        ref = item.get("value") if isinstance(item, dict) else None
        if not isinstance(ref, str):
            raise ScimError(400, "each member needs a value", "invalidValue")
        user = await _user(db, tenant_id, ref)
        exists = (
            await db.execute(
                select(GuideScimMember.id).where(
                    GuideScimMember.group_id == group.id, GuideScimMember.user_id == user.id
                )
            )
        ).first()
        if exists is None:
            db.add(GuideScimMember(tenant_id=tenant_id, group_id=group.id, user_id=user.id))


async def _remove_member(db: AsyncSession, group: GuideScimGroup, ref: str) -> None:
    try:
        user_id = uuid.UUID(ref)
    except ValueError:
        return
    await db.execute(
        delete(GuideScimMember).where(
            GuideScimMember.group_id == group.id, GuideScimMember.user_id == user_id
        )
    )


def _operations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    ops = payload.get("Operations")
    if not isinstance(ops, list) or not all(isinstance(o, dict) for o in ops):
        raise ScimError(400, "Operations must be a list", "invalidSyntax")
    return ops


async def _display_name_taken(
    db: AsyncSession, tenant_id: uuid.UUID, name: str, other_than: uuid.UUID | None = None
) -> bool:
    query = select(GuideScimGroup.id).where(
        GuideScimGroup.tenant_id == tenant_id, GuideScimGroup.display_name == name
    )
    if other_than is not None:
        query = query.where(GuideScimGroup.id != other_than)
    # Not flushed first: a rename to a taken name would hit the unique constraint
    # in the flush and never reach the check.
    with db.no_autoflush:
        return (await db.execute(query)).first() is not None


@router.get("/Groups")
async def list_groups(
    request: Request,
    tenant_id: uuid.UUID = Depends(require_scim_tenant),
    db: AsyncSession = DB_SESSION,
    filter: str | None = Query(None),  # noqa: A002
    startIndex: int = Query(1, ge=1),  # noqa: N803
    count: int = Query(100, ge=0),
) -> Response:
    try:
        clause = _filter(
            filter,
            {
                "displayname": lambda v: GuideScimGroup.display_name == v,
                "externalid": lambda v: GuideScimGroup.external_id == v,
            },
        )
    except ScimError as error:
        return _error(error)
    excluded = (request.query_params.get("excludedAttributes") or "").lower()
    base = select(GuideScimGroup).where(GuideScimGroup.tenant_id == tenant_id)
    if clause is not None:
        base = base.where(clause)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        (
            await db.execute(
                base.order_by(GuideScimGroup.display_name)
                .offset(startIndex - 1)
                .limit(min(count, MAX_PAGE))
            )
        )
        .scalars()
        .all()
    )
    return _scim(
        {
            "schemas": [LIST_SCHEMA],
            "totalResults": total,
            "startIndex": startIndex,
            "itemsPerPage": len(rows),
            "Resources": [await _group_json(db, g, "members" not in excluded) for g in rows],
        }
    )


@router.post("/Groups")
async def create_group(
    request: Request,
    payload: dict[str, Any] = Body(...),
    tenant_id: uuid.UUID = Depends(require_scim_tenant),
    db: AsyncSession = DB_SESSION,
) -> Response:
    try:
        name = _text(payload.get("displayName"), "displayName", 255)
        if await _display_name_taken(db, tenant_id, name):
            raise ScimError(409, "displayName is already provisioned", "uniqueness")
        external = payload.get("externalId")
        group = GuideScimGroup(
            tenant_id=tenant_id,
            display_name=name,
            external_id=None if external is None else _text(external, "externalId", 255),
        )
        db.add(group)
        await _write(db, "displayName", commit=False)
        if payload.get("members"):
            await _add_members(db, tenant_id, group, payload["members"])
        await _write(db, "displayName", commit=False)
        details = {"displayName": group.display_name, "externalId": group.external_id}
        details |= _member_details(await _member_ids(db, group.id), set())
        await _audit(db, request, tenant_id, "scim.group.created", group.id, details)
        await db.commit()
    except ScimError as error:
        await db.rollback()
        return _error(error)
    await db.refresh(group)
    return _scim(await _group_json(db, group), 201)


@router.get("/Groups/{group_id}")
async def get_group(
    group_id: str,
    request: Request,
    tenant_id: uuid.UUID = Depends(require_scim_tenant),
    db: AsyncSession = DB_SESSION,
) -> Response:
    excluded = (request.query_params.get("excludedAttributes") or "").lower()
    try:
        group = await _group(db, tenant_id, group_id)
    except ScimError as error:
        return _error(error)
    return _scim(await _group_json(db, group, "members" not in excluded))


@router.patch("/Groups/{group_id}")
async def patch_group(
    group_id: str,
    request: Request,
    payload: dict[str, Any] = Body(...),
    tenant_id: uuid.UUID = Depends(require_scim_tenant),
    db: AsyncSession = DB_SESSION,
) -> Response:
    try:
        group = await _group(db, tenant_id, group_id)
        before = {"displayName": group.display_name, "externalId": group.external_id}
        members_before = await _member_ids(db, group.id)
        for op in _operations(payload):
            kind = str(op.get("op", "")).lower()
            path = str(op.get("path") or "")
            value = op.get("value")
            if kind == "add" and path.lower() == "members":
                await _add_members(db, tenant_id, group, value)
            elif kind == "remove" and path.lower() == "members":
                for item in value if isinstance(value, list) else []:
                    if isinstance(item, dict) and isinstance(item.get("value"), str):
                        await _remove_member(db, group, item["value"])
            elif kind == "remove" and (match := _MEMBER_PATH_RE.match(path)):
                await _remove_member(db, group, match.group(1))
            elif kind == "replace" and path.lower() == "members":
                await db.execute(
                    delete(GuideScimMember).where(GuideScimMember.group_id == group.id)
                )
                await _add_members(db, tenant_id, group, value)
            elif kind in ("replace", "add") and (
                path.lower() == "displayname" or (not path and isinstance(value, dict))
            ):
                name = value.get("displayName") if isinstance(value, dict) else value
                if name is not None:
                    name = _text(name, "displayName", 255)
                    if await _display_name_taken(db, tenant_id, name, other_than=group.id):
                        raise ScimError(409, "displayName is already provisioned", "uniqueness")
                    group.display_name = name
            elif kind in ("replace", "add") and path.lower() == "externalid":
                group.external_id = None if value is None else _text(value, "externalId", 255)
            # Anything else names an attribute this store does not keep.
        await _write(db, "displayName", commit=False)
        after = {"displayName": group.display_name, "externalId": group.external_id}
        members_after = await _member_ids(db, group.id)
        details = _changes(before, after) | _member_details(
            members_after - members_before, members_before - members_after
        )
        if details:
            await _audit(db, request, tenant_id, "scim.group.updated", group.id, details)
        await db.commit()
    except ScimError as error:
        await db.rollback()
        return _error(error)
    await db.refresh(group)
    return _scim(await _group_json(db, group))


@router.delete("/Groups/{group_id}")
async def delete_group(
    group_id: str,
    request: Request,
    tenant_id: uuid.UUID = Depends(require_scim_tenant),
    db: AsyncSession = DB_SESSION,
) -> Response:
    try:
        group = await _group(db, tenant_id, group_id)
    except ScimError as error:
        return _error(error)
    details = {
        "displayName": group.display_name,
        "externalId": group.external_id,
        "membersLeft": len(await _member_ids(db, group.id)),
    }
    await db.execute(delete(GuideScimMember).where(GuideScimMember.group_id == group.id))
    await db.delete(group)
    await _audit(db, request, tenant_id, "scim.group.deleted", group.id, details)
    await db.commit()
    return Response(status_code=204)


@router.get("/ServiceProviderConfig")
async def service_provider_config(
    tenant_id: uuid.UUID = Depends(require_scim_tenant),
) -> Response:
    return _scim(
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
            "patch": {"supported": True},
            "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
            "filter": {"supported": True, "maxResults": MAX_PAGE},
            "changePassword": {"supported": False},
            "sort": {"supported": False},
            "etag": {"supported": False},
            "authenticationSchemes": [
                {
                    "type": "oauthbearertoken",
                    "name": "OAuth Bearer Token",
                    "description": "The tenant's SCIM token, made in Atlas",
                }
            ],
        }
    )
