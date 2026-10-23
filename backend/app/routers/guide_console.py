"""Promptly Guide's admin console — `/api/v1/guide/console` (promptly-guide N1, option A).

Admin (Atlas JWT), tenant admins only:
  GET  /guide/console                        the settings and the files made from them
  PUT  /guide/console                        save the settings (audited)
  GET  /guide/console/profiles/{file_name}   one configuration profile (.mobileconfig)
  GET  /guide/console/profiles.zip           every configuration profile (audited)
  GET  /guide/console/windows.zip            the ADMX, ADML and test .reg files (audited)

Atlas holds the same settings as Guide's file console and turns them into the files an
admin uploads to Jamf or Intune. It never writes to a Mac or a PC itself, and calls no
MDM API: under option A the MDM stays the only channel that changes a Mac. Writing
through the MDM's API is a later step.

The settings only restrict or name content: a document with any other field is
refused, word for word as the file console refuses it (``profiles.build``). Nothing
here can turn on something that reads; the person's own opt-ins stay on their Mac.
"""

from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile
from typing import Any

from fastapi import APIRouter, Depends
from fastapi import status as http_status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, TenantAdmin, get_tenant_db_session
from app.errors import AppException, ConflictError, ForbiddenError, NotFoundError
from app.models.guide_console import GuideConsoleSettings
from app.schemas.guide_console import GuideConsoleFileOut, GuideConsoleIn, GuideConsoleOut
from app.services.audit import log_audit_event
from app.services.guide_console import profiles, windows

router = APIRouter(prefix="/guide/console", tags=["Promptly Guide"])

MOBILECONFIG_TYPE = "application/x-apple-aspen-config"
# A console document is a few kilobytes; this leaves room for hundreds of entries.
MAX_CONSOLE_BYTES = 256 * 1024
# Zip entries carry this date, so the same settings always give the same archive.
_ZIP_DATE = (2026, 1, 1, 0, 0, 0)
_GROUP_PREFIX = "promptly-guide-group-"
_PROFILE_SUFFIX = ".mobileconfig"


def _tenant_of(user: CurrentUser) -> uuid.UUID:
    if user.tenant_id is None:
        raise ForbiddenError("Tenant context required")
    return user.tenant_id


async def _row(db: AsyncSession, tenant_id: uuid.UUID) -> GuideConsoleSettings | None:
    return (
        await db.execute(
            select(GuideConsoleSettings).where(GuideConsoleSettings.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()


def _invalid(error: profiles.ConsoleError) -> AppException:
    return AppException(
        code="GUIDE_CONSOLE_INVALID",
        message=str(error),
        status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


def _build(console: dict[str, Any]) -> tuple[dict[str, dict], dict[str, bytes], list[str]]:
    """The Mac profiles, the Windows files and their notes. Raises ConsoleError."""
    mac = profiles.build(console)
    win, notes = windows.files(console)
    return mac, win, notes


def _group(file_name: str) -> str | None:
    if file_name.startswith(_GROUP_PREFIX) and file_name.endswith(_PROFILE_SUFFIX):
        return file_name[len(_GROUP_PREFIX) : -len(_PROFILE_SUFFIX)]
    return None


def _out(row: GuideConsoleSettings | None) -> GuideConsoleOut:
    if row is None:
        return GuideConsoleOut(
            console=None,
            revision=0,
            updated_at=None,
            updated_by=None,
            profiles=[],
            windows_files=[],
            windows_notes=[],
            problem=None,
        )
    try:
        mac, win, notes = _build(row.console)
    except profiles.ConsoleError as error:
        # Saved under an earlier check that this Guide no longer accepts (a pack it
        # dropped, say). Shown, so the admin can fix it; nothing is offered to download.
        return GuideConsoleOut(
            console=row.console,
            revision=row.revision,
            updated_at=row.updated_at,
            updated_by=row.updated_by,
            profiles=[],
            windows_files=[],
            windows_notes=[],
            problem=str(error),
        )
    return GuideConsoleOut(
        console=row.console,
        revision=row.revision,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
        profiles=[GuideConsoleFileOut(name=n, group=_group(n)) for n in mac],
        windows_files=[GuideConsoleFileOut(name=n) for n in win],
        windows_notes=notes,
        problem=None,
    )


def _settings(console: dict[str, Any] | None) -> dict[str, dict]:
    """{profile file name: what it forces}, as the file console compares them."""
    if console is None:
        return {}
    try:
        built = profiles.build(console)
    except profiles.ConsoleError:
        # Saved under an earlier check this Guide refuses: nothing it would write.
        return {}
    return {name: profiles.settings_of(p) for name, p in built.items()}


def _sha256(console: dict[str, Any]) -> str:
    text = json.dumps(console, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, files[name])
    return buffer.getvalue()


def _attachment(content: bytes, file_name: str, media_type: str) -> Response:
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


async def _saved(db: AsyncSession, user: CurrentUser) -> GuideConsoleSettings:
    row = await _row(db, _tenant_of(user))
    if row is None:
        raise NotFoundError("Guide console settings")
    try:
        _build(row.console)
    except profiles.ConsoleError as error:
        raise _invalid(error)
    return row


async def _log_download(
    db: AsyncSession, user: CurrentUser, row: GuideConsoleSettings, files: dict[str, bytes]
) -> None:
    """Which files left Atlas, from which revision, with a hash of each, so a profile
    found in the MDM can be traced to the settings it came from."""
    await log_audit_event(
        db,
        event_type="guide.console.downloaded",
        action="export",
        actor_id=user.user_id,
        actor_email=user.email,
        tenant_id=row.tenant_id,
        resource_type="guide_console_settings",
        resource_id=str(row.id),
        details={
            "revision": row.revision,
            "files": {name: hashlib.sha256(files[name]).hexdigest() for name in sorted(files)},
        },
    )


@router.get("", response_model=GuideConsoleOut)
async def get_console(
    user: TenantAdmin,
    db: AsyncSession = Depends(get_tenant_db_session),
) -> GuideConsoleOut:
    return _out(await _row(db, _tenant_of(user)))


@router.put("", response_model=GuideConsoleOut)
async def put_console(
    payload: GuideConsoleIn,
    user: TenantAdmin,
    db: AsyncSession = Depends(get_tenant_db_session),
) -> GuideConsoleOut:
    """Save the settings. Refused, with the file console's own words, when the file
    console would refuse them. A save that changes something is written to the audit
    log with every changed key, old and new; saving the same settings writes nothing."""
    tenant_id = _tenant_of(user)
    console = payload.console
    if len(json.dumps(console).encode("utf-8")) > MAX_CONSOLE_BYTES:
        raise AppException(
            code="GUIDE_CONSOLE_TOO_LARGE",
            message=f"The settings are larger than {MAX_CONSOLE_BYTES // 1024} KB.",
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    try:
        _build(console)
    except profiles.ConsoleError as error:
        raise _invalid(error)

    row = await _row(db, tenant_id)
    previous = row.console if row is not None else None
    if previous != console:
        found = profiles.changes(_settings(previous), _settings(console))
        if row is None:
            row = GuideConsoleSettings(tenant_id=tenant_id, console=console, revision=1)
            db.add(row)
        else:
            row.console = console
            row.revision += 1
        row.updated_by = user.email
        try:
            await db.flush()
        except IntegrityError:
            # Another admin saved the first settings at the same moment.
            await db.rollback()
            raise ConflictError("The settings were just saved by someone else. Load them again.")
        details: dict[str, Any] = {
            "revision": row.revision,
            "console_sha256": _sha256(console),
            "changes": found,
        }
        if payload.note:
            details["note"] = payload.note
        await log_audit_event(
            db,
            event_type="guide.console.updated",
            action="create" if previous is None else "update",
            actor_id=user.user_id,
            actor_email=user.email,
            tenant_id=tenant_id,
            resource_type="guide_console_settings",
            resource_id=str(row.id),
            details=details,
        )
        await db.refresh(row)
    # Read before the commit: the tenant setting for RLS lasts one transaction.
    result = _out(row)
    await db.commit()
    return result


@router.get("/profiles.zip")
async def download_profiles(
    user: TenantAdmin,
    db: AsyncSession = Depends(get_tenant_db_session),
) -> Response:
    """Every configuration profile, in one zip: upload each to Jamf or Intune, scope the
    one for everyone to every Mac and each group's to its group."""
    row = await _saved(db, user)
    files = {n: profiles.profile_bytes(p) for n, p in profiles.build(row.console).items()}
    await _log_download(db, user, row, files)
    await db.commit()
    return _attachment(_zip(files), "promptly-guide-profiles.zip", "application/zip")


@router.get("/windows.zip")
async def download_windows(
    user: TenantAdmin,
    db: AsyncSession = Depends(get_tenant_db_session),
) -> Response:
    """The ADMX template and its en-US ADML for Intune or Group Policy, and .reg files
    for trying a build on a test PC."""
    row = await _saved(db, user)
    files, _ = windows.files(row.console)
    await _log_download(db, user, row, files)
    await db.commit()
    return _attachment(_zip(files), "promptly-guide-windows.zip", "application/zip")


@router.get("/profiles/{file_name}")
async def download_profile(
    file_name: str,
    user: TenantAdmin,
    db: AsyncSession = Depends(get_tenant_db_session),
) -> Response:
    """One configuration profile, as the file console would write it."""
    row = await _saved(db, user)
    built = profiles.build(row.console)
    if file_name not in built:
        raise NotFoundError("Profile", file_name)
    files = {file_name: profiles.profile_bytes(built[file_name])}
    await _log_download(db, user, row, files)
    await db.commit()
    return _attachment(files[file_name], file_name, MOBILECONFIG_TYPE)
