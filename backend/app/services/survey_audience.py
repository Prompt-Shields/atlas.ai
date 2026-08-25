"""Audience resolver — turn an audience filter into a recipient list.

Pure service. Given a tenant_id + audience filter shape + a DB
session, returns the list of recipient identities to dispatch the
survey to.

Audience filter shapes (mirrors the frontend modal in PR #25):

    {"mode": "all"}
    {"mode": "departments", "values": ["Grants Management", "Finance"]}
    {"mode": "tools",       "values": ["Microsoft Copilot"]}
    {"mode": "custom",      "values": ["alice@x.com", "@bob"]}

In PR B the resolver leans on the existing `User` table only — we
don't yet have a `Person` / department-mapping table, so `mode=
departments` and `mode=tools` degrade gracefully (the filter is
recorded in audience_filter_json for future replay; in this PR's
implementation we resolve to all tenant users).

PR future:
  - department mapping arrives when the M3 Person table lands
  - tool-detection mapping arrives with the Promptly extension's
    detection writes
Both are additive — the dispatcher's call shape stays the same.

slack_user_id is **not** populated here. PR C's Slack adapter does
the `users.lookupByEmail` round-trip from `recipient_email`.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppException
from app.models.user import User


class AudienceValidationError(AppException):
    """Audience filter shape is malformed. Routers translate to 400."""

    def __init__(self, message: str) -> None:
        super().__init__(
            code="AUDIENCE_INVALID",
            message=message,
            status_code=400,
        )


@dataclass(frozen=True)
class RecipientIdentity:
    """A single recipient to dispatch one DM / email to."""

    user_id: uuid.UUID | None
    email: str | None
    label: str  # display name or email — for the per-row debug log


# Email-ish regex — same loose pattern the rest of the codebase uses
# (we let Pydantic email-validator handle strict checks elsewhere).
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _parse_filter(raw_json: str | dict[str, Any]) -> dict[str, Any]:
    """Decode the audience_filter_json into a dict, raising on shape errors."""
    if isinstance(raw_json, str):
        try:
            decoded = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise AudienceValidationError(f"audience filter is not valid JSON: {exc.msg}") from exc
    else:
        decoded = raw_json

    if not isinstance(decoded, dict):
        raise AudienceValidationError("audience filter must be an object")
    mode = decoded.get("mode")
    if mode not in {"all", "departments", "tools", "custom"}:
        raise AudienceValidationError(
            f"audience.mode must be one of all|departments|tools|custom, got {mode!r}"
        )
    return decoded


def label_for_filter(raw_filter: str | dict[str, Any]) -> str:
    """A short human label cached on the SurveyDelivery row."""
    f = _parse_filter(raw_filter)
    mode = f["mode"]
    values = f.get("values") or []
    if mode == "all":
        return "All staff"
    if mode == "departments":
        if not values:
            return "No departments selected"
        if len(values) == 1:
            return f"Department: {values[0]}"
        return f"{len(values)} departments"
    if mode == "tools":
        return "Detected on " + ", ".join(values) if values else "No tools selected"
    if mode == "custom":
        return f"Custom list ({len(values)})"
    return mode


async def resolve_recipients(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    raw_filter: str | dict[str, Any],
) -> list[RecipientIdentity]:
    """Return the recipients for a given audience filter.

    Tenant-scoped — never returns users outside `tenant_id`. The
    return list is de-duplicated by `user_id` (or by `email` when
    `user_id` is None).

    Resolution strategy:
      - `custom`: parse the email list. Slack handles skipped (PR C).
      - `all` / `departments` / `tools`:
          - If a Microsoft Entra ID integration has ever synced
            (i.e. DirectoryUser rows exist for this tenant), resolve
            against DirectoryUser — supports the `departments` filter
            for real.
          - Otherwise fall back to atlas's User table — every
            authenticated user gets the survey (the PR B default).
    """
    f = _parse_filter(raw_filter)
    mode = f["mode"]
    values = f.get("values") or []

    if mode == "custom":
        return _resolve_custom_list(values)

    if mode not in {"all", "departments", "tools"}:
        # Belt-and-braces — _parse_filter already rejected unknown modes.
        raise AudienceValidationError(f"unhandled audience.mode {mode!r}")

    # Try DirectoryUser first (Entra ID sync output).
    directory_recipients = await _resolve_via_directory(db, tenant_id, mode, values)
    if directory_recipients is not None:
        return _dedupe(directory_recipients)

    # Fallback: atlas's User table (login identities).
    query = select(User).where(User.tenant_id == tenant_id).where(User.is_active.is_(True))
    result = await db.execute(query)
    users = list(result.scalars().all())
    return _dedupe(
        [
            RecipientIdentity(
                user_id=u.id,
                email=u.email.lower() if u.email else None,
                label=u.full_name or u.email,
            )
            for u in users
        ]
    )


async def _resolve_via_directory(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    mode: str,
    values: list[str],
) -> list[RecipientIdentity] | None:
    """Resolve via DirectoryUser — None if no directory ever synced.

    For `mode=departments`, filter by the (now real) `department`
    column. For `mode=tools`, we don't yet have a join between
    DirectoryUser and tool-detection — degrade to all directory
    users. For `mode=all`, all of them.
    """
    # Import here to keep the symbol-table small for callers that
    # never sync from a directory.
    from app.models.directory import DirectoryUser

    base = select(DirectoryUser).where(
        DirectoryUser.tenant_id == tenant_id,
        DirectoryUser.is_active.is_(True),
    )
    # If the directory is empty for this tenant, return None to
    # trigger the fallback path.
    any_q = select(DirectoryUser.id).where(DirectoryUser.tenant_id == tenant_id).limit(1)
    any_row = (await db.execute(any_q)).scalar_one_or_none()
    if any_row is None:
        return None

    if mode == "departments" and values:
        base = base.where(DirectoryUser.department.in_(values))

    result = await db.execute(base)
    rows = list(result.scalars().all())
    return [
        RecipientIdentity(
            user_id=r.linked_user_id,
            email=r.email,
            label=r.display_name or r.email or "(no name)",
        )
        for r in rows
    ]


def _resolve_custom_list(values: list[Any]) -> list[RecipientIdentity]:
    """Parse a custom audience list into RecipientIdentity rows.

    Accepts entries that look like emails. Non-email entries (e.g.
    `@slack-handle`) are skipped in PR B — slack-handle resolution
    is PR C's job (Slack `users.lookupByHandle`).
    """
    recipients: list[RecipientIdentity] = []
    for item in values:
        if not isinstance(item, str):
            raise AudienceValidationError("custom audience values must be strings")
        candidate = item.strip().lower()
        if not candidate:
            continue
        if not _EMAIL_RE.match(candidate):
            # Skip Slack handles in PR B; PR C resolves these.
            continue
        recipients.append(
            RecipientIdentity(
                user_id=None,
                email=candidate,
                label=candidate,
            )
        )
    return _dedupe(recipients)


def _dedupe(recipients: list[RecipientIdentity]) -> list[RecipientIdentity]:
    """De-dupe by user_id (when present) or email (when not)."""
    seen_users: set[uuid.UUID] = set()
    seen_emails: set[str] = set()
    out: list[RecipientIdentity] = []
    for r in recipients:
        if r.user_id is not None:
            if r.user_id in seen_users:
                continue
            seen_users.add(r.user_id)
            if r.email:
                seen_emails.add(r.email)
            out.append(r)
        elif r.email:
            if r.email in seen_emails:
                continue
            seen_emails.add(r.email)
            out.append(r)
    return out
