"""JumpCloud Directory API client — thin, mockable, httpx-based.

JumpCloud combines directory (users + groups) and MDM (systems) in
one platform. Atlas uses the Systems API to populate the cross-MDM
endpoint dashboard; user/group sync to a future DirectoryUser-shape
table lands when customers ask (likely v0.3 if they're using
JumpCloud as their primary IdP instead of Entra ID).

Auth: x-api-key header. The API key is generated in the JumpCloud
console (Settings → API Settings). It's a per-tenant secret — no
OAuth, no bearer dance.

API surface used (v0.2):
  GET /api/systems              paginated system roster
  GET /api/v2/systemgroups      device groups

Pagination: `limit` + `skip` query params. Note the v1 systems
endpoint uses {results: [...], totalCount} envelope; the v2 endpoint
returns bare arrays.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx

# ─── Errors ──────────────────────────────────────────────────────────


class JumpCloudAPIError(Exception):
    """Non-2xx from JumpCloud."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"jumpcloud {status_code}: {message}")


class JumpCloudAuthError(JumpCloudAPIError):
    """401 / 403 from JumpCloud."""


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.is_success:
        return
    msg = _safe_error(resp)
    if resp.status_code in (401, 403):
        raise JumpCloudAuthError(resp.status_code, msg)
    raise JumpCloudAPIError(resp.status_code, msg)


# ─── Low-level GET wrapper ───────────────────────────────────────────


JUMPCLOUD_BASE = "https://console.jumpcloud.com"


async def jumpcloud_get(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    path: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    resp = await client.get(
        f"{JUMPCLOUD_BASE}{path}",
        headers={
            "x-api-key": api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        params=params,
        timeout=20.0,
    )
    _raise_for_status(resp)
    return resp.json()


# ─── System iterator (v1 endpoint uses {results, totalCount}) ───────


async def iter_systems(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    page_size: int = 100,
) -> AsyncIterator[dict[str, Any]]:
    """Yield every system (managed device) in the JumpCloud tenant."""
    skip = 0
    while True:
        body = await jumpcloud_get(
            client,
            api_key=api_key,
            path="/api/systems",
            params={
                "limit": page_size,
                "skip": skip,
                "fields": (
                    "_id displayName hostname systemType os version active lastContact agentVersion"
                ),
            },
        )
        if not isinstance(body, dict):
            return
        items = body.get("results") or []
        total = int(body.get("totalCount", 0) or 0)
        for item in items:
            yield item
        seen = skip + len(items)
        if not items or seen >= total:
            return
        skip = seen


# ─── System group iterator (v2 endpoint, bare arrays) ───────────────


async def iter_system_groups(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    page_size: int = 100,
) -> AsyncIterator[dict[str, Any]]:
    """Yield every system group."""
    skip = 0
    while True:
        body = await jumpcloud_get(
            client,
            api_key=api_key,
            path="/api/v2/systemgroups",
            params={"limit": page_size, "skip": skip},
        )
        if not isinstance(body, list):
            return
        if not body:
            return
        for item in body:
            yield item
        if len(body) < page_size:
            return
        skip += page_size


# ─── Normalisation ───────────────────────────────────────────────────


def normalise_system(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a JumpCloud system payload → ManagedDevice-shape dict.

    JumpCloud field names:
      _id, displayName, hostname, systemType, os, version, active,
      lastContact, agentVersion

    systemType values: "darwin" | "windows" | "linux".
    """
    external_id = str(raw.get("_id") or raw.get("id") or "").strip()
    if not external_id:
        return None

    display_name = (raw.get("displayName") or raw.get("hostname") or "").strip() or "(unnamed)"

    system_type = (raw.get("systemType") or "").strip().lower()
    os_label = _systype_to_os(system_type, raw.get("os"))

    return {
        "external_device_id": external_id,
        "device_name": display_name,
        "operating_system": os_label,
        "os_version": _strip_or_none(raw.get("version")),
        "compliance_state": _derive_compliance(raw),
        # JumpCloud doesn't carry a UPN on the system row itself
        # (binding to a user is via /systems/{id}/users — not pulled
        # in v0.2 to keep the sync simple). Left null.
        "enrolled_user_email": None,
        "last_sync_to_mdm_at": _parse_iso(raw.get("lastContact")),
    }


def normalise_system_group(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "external_group_id": str(raw.get("id") or raw.get("_id") or "").strip(),
        "display_name": (raw.get("name") or "").strip() or "(unnamed)",
        "description": _strip_or_none(raw.get("description")),
        "group_type": (raw.get("type") or "system_group"),
    }


# ─── Helpers ─────────────────────────────────────────────────────────


def _strip_or_none(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s or None


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _safe_error(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        if isinstance(body, dict):
            # JumpCloud error envelopes: {"message": "..."} or
            # {"error": "..."}.
            for key in ("message", "error", "detail"):
                if key in body:
                    return str(body[key])[:200]
            return str(body)[:200]
    except (ValueError, TypeError):
        pass
    return resp.text[:200]


def _systype_to_os(system_type: str, os_name: Any) -> str:
    """JumpCloud uses `systemType` darwin / windows / linux + a free-form
    `os` field. We collapse to atlas's standard OS labels.
    """
    if system_type == "darwin":
        return "macOS"
    if system_type == "windows":
        return "Windows"
    if system_type == "linux":
        return "Linux"
    if isinstance(os_name, str) and os_name.strip():
        return os_name.strip()
    return "Unknown"


def _derive_compliance(raw: dict[str, Any]) -> str:
    """JumpCloud surfaces `active` + `lastContact`. We map:
    - active=false               → noncompliant (device offboarded or stale)
    - active=true + lastContact within 7 days → compliant
    - active=true + stale        → noncompliant
    - active missing             → unknown
    """
    if raw.get("active") is False:
        return "noncompliant"
    if raw.get("active") is None:
        return "unknown"
    last = _parse_iso(raw.get("lastContact"))
    if last is None:
        return "noncompliant"
    now = datetime.now(last.tzinfo)
    days_since = (now - last).total_seconds() / 86400.0
    if days_since > 7:
        return "noncompliant"
    return "compliant"
