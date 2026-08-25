"""Jamf Pro API client — thin, mockable, httpx-based.

Jamf Pro authenticates differently from Microsoft Graph:

  - Basic auth (username:password) to POST /api/v1/auth/token
    returns a bearer token valid for 30 minutes.
  - All subsequent calls go to /api/v1/computers-inventory etc.
    with Authorization: Bearer <token>.
  - Tokens can be invalidated (POST /api/v1/auth/invalidate-token)
    or kept alive (POST /api/v1/auth/keep-alive). For sync we just
    auth-once-per-run and discard.

API surface used:
  - POST /api/v1/auth/token              — obtain bearer
  - GET  /api/v1/computers-inventory     — paginated computers
  - GET  /api/v1/computer-groups         — smart + static groups
  - POST /api/v1/auth/invalidate-token   — best-effort cleanup

Cursor-based pagination via `page` + `page-size` query params;
response carries `totalCount`. Cleaner than Microsoft's
@odata.nextLink continuation.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx

# ─── Errors ──────────────────────────────────────────────────────────


class JamfAPIError(Exception):
    """Jamf returned a non-2xx with a structured error."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"jamf {status_code}: {message}")


class JamfAuthError(JamfAPIError):
    """401 from Jamf — bad credentials, expired bearer, or revoked."""


# ─── Auth ────────────────────────────────────────────────────────────


async def acquire_bearer_token(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    username: str,
    password: str,
) -> dict[str, Any]:
    """POST /api/v1/auth/token — exchange Basic auth for a bearer.

    Returns:
      { "token": str, "expires": datetime }
    """
    base = base_url.rstrip("/")
    cred = f"{username}:{password}".encode()
    encoded = base64.b64encode(cred).decode("ascii")
    resp = await client.post(
        f"{base}/api/v1/auth/token",
        headers={"Authorization": f"Basic {encoded}"},
        timeout=15.0,
    )
    if resp.status_code == 401:
        raise JamfAuthError(401, "Invalid username or password")
    if not resp.is_success:
        raise JamfAPIError(resp.status_code, _safe_error(resp))
    data = resp.json()
    if "token" not in data:
        raise JamfAPIError(200, "auth response missing 'token'")
    return {
        "token": data["token"],
        "expires": _parse_iso(data.get("expires")),
    }


async def invalidate_bearer_token(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    token: str,
) -> None:
    """Best-effort token cleanup. Failure is ignored."""
    base = base_url.rstrip("/")
    try:
        await client.post(
            f"{base}/api/v1/auth/invalidate-token",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5.0,
        )
    except httpx.HTTPError:
        # Cleanup is best-effort; the token expires on its own in 30
        # minutes if we can't proactively revoke it.
        pass


# ─── Low-level GET wrapper ───────────────────────────────────────────


async def jamf_get(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    token: str,
    path: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base = base_url.rstrip("/")
    resp = await client.get(
        f"{base}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=20.0,
    )
    if resp.status_code == 401:
        raise JamfAuthError(401, "Bearer token rejected")
    if not resp.is_success:
        raise JamfAPIError(resp.status_code, _safe_error(resp))
    return resp.json()


# ─── Computer iterator ───────────────────────────────────────────────


async def iter_computers(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    token: str,
    page_size: int = 100,
) -> AsyncIterator[dict[str, Any]]:
    """Yield every computer in the Jamf Pro inventory.

    Uses `section=GENERAL,HARDWARE,USER_AND_LOCATION` to keep the
    response payload focused on what we map to ManagedDevice fields.
    Pagination via page + page-size; loop until total reached.
    """
    page = 0
    while True:
        body = await jamf_get(
            client,
            base_url=base_url,
            token=token,
            path="/api/v1/computers-inventory",
            params={
                "page": page,
                "page-size": page_size,
                "section": "GENERAL,HARDWARE,USER_AND_LOCATION",
            },
        )
        items = body.get("results") or []
        for item in items:
            yield item
        total = int(body.get("totalCount", 0) or 0)
        seen = (page + 1) * page_size
        if not items or seen >= total:
            return
        page += 1


async def iter_groups(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    token: str,
    page_size: int = 100,
) -> AsyncIterator[dict[str, Any]]:
    """Yield every computer group (smart + static)."""
    page = 0
    while True:
        body = await jamf_get(
            client,
            base_url=base_url,
            token=token,
            path="/api/v1/computer-groups",
            params={"page": page, "page-size": page_size},
        )
        items = body.get("results") or []
        for item in items:
            yield item
        total = int(body.get("totalCount", 0) or 0)
        seen = (page + 1) * page_size
        if not items or seen >= total:
            return
        page += 1


# ─── Normalisation ───────────────────────────────────────────────────


def normalise_computer(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a Jamf Pro computer payload → DirectoryDevice-shape dict.

    The shape mirrors what IntuneDevice / future ManagedDevice expect:
      - external_device_id, device_name, operating_system,
        os_version, compliance_state (Jamf has no direct
        compliance state — derived from gatekeeper + filevault),
        enrolled_user_email, last_sync_to_mdm_at

    Returns None if the row is missing the primary identifier.
    """
    general = raw.get("general") or {}
    hardware = raw.get("hardware") or {}
    user_location = raw.get("userAndLocation") or {}

    external_id = str(raw.get("id") or general.get("id") or "").strip()
    if not external_id:
        return None

    device_name = (general.get("name") or general.get("deviceName") or "").strip() or "(unnamed)"

    last_inventory = _parse_iso(general.get("lastInventoryUpdateDate"))

    # Jamf Pro doesn't have a single `complianceState`; we derive one.
    compliance = _derive_compliance_state(general, hardware)

    return {
        "external_device_id": external_id,
        "device_name": device_name,
        "operating_system": "macOS",
        "os_version": _strip_or_none(hardware.get("osVersion")),
        "compliance_state": compliance,
        "enrolled_user_email": _lower_or_none(
            user_location.get("email")
            or user_location.get("realName")
            or user_location.get("username"),
        ),
        "last_sync_to_mdm_at": last_inventory,
    }


def normalise_group(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "external_group_id": str(raw.get("id") or "").strip(),
        "display_name": (raw.get("name") or "").strip() or "(unnamed)",
        "description": _strip_or_none(raw.get("comments")),
        "group_type": "Smart" if bool(raw.get("isSmart")) else "Static",
    }


# ─── Helpers ─────────────────────────────────────────────────────────


def _strip_or_none(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s or None


def _lower_or_none(value: Any) -> str | None:
    s = _strip_or_none(value)
    return s.lower() if s else None


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
            # Jamf Pro error shape: {"httpStatus":401,"errors":[{"code":"INVALID_TOKEN","description":"..."}]}
            errors = body.get("errors") or []
            if errors and isinstance(errors, list):
                first = errors[0]
                if isinstance(first, dict):
                    return f"{first.get('code', 'unknown')}: {first.get('description', '')}"
            return str(body)[:200]
    except (ValueError, TypeError):
        pass
    return resp.text[:200]


def _derive_compliance_state(
    general: dict[str, Any],
    hardware: dict[str, Any],
) -> str:
    """Derive a compliance state from Jamf signal mix.

    Jamf Pro doesn't expose Intune-style `complianceState`; we synth
    one from gatekeeper + FileVault + MDM-managed flags.

    - Both gatekeeper + FileVault enabled + MDM-managed → compliant
    - MDM-managed but FileVault disabled → noncompliant
    - Not MDM-managed → unknown
    """
    is_managed = bool(general.get("managementId") or general.get("mdmCapable"))
    if not is_managed:
        return "unknown"
    # Gatekeeper is ok if explicitly enabled. Missing / DISABLED →
    # treat as not-ok (Jamf returns "ENABLED" / "DISABLED" / null).
    gatekeeper_ok = hardware.get("gatekeeperStatus") == "ENABLED"
    filevault_ok = (
        hardware.get("fileVault2Eligibility") == "ELIGIBLE"
        or hardware.get("fileVault2Status") == "ENABLED"
    )
    if gatekeeper_ok and filevault_ok:
        return "compliant"
    return "noncompliant"
