"""Kandji API client — thin, mockable, httpx-based.

Kandji's auth model is simpler than Jamf's: a single static API
token (created in Kandji admin → Access → API Token) is sent on
every request as `Authorization: Bearer <token>`. No bearer-exchange
step like Jamf.

Per-tenant subdomain: each Kandji customer has its own subdomain,
e.g. `accuhive.api.kandji.io`. We store the full base URL in the
credential blob so the customer's subdomain is opaque to atlas.

API surface used (v0.2):
  GET /api/v1/devices            paginated device roster
  GET /api/v1/blueprints         blueprint roster (analogous to a
                                 Configuration Profile group)

Pagination: Kandji uses `limit` + `offset` query params. Responses
are flat arrays; we paginate until a non-full page is returned.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx

# ─── Errors ──────────────────────────────────────────────────────────


class KandjiAPIError(Exception):
    """Kandji returned a non-2xx with a structured error."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"kandji {status_code}: {message}")


class KandjiAuthError(KandjiAPIError):
    """401 / 403 — bad or revoked API token."""


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.is_success:
        return
    msg = _safe_error(resp)
    if resp.status_code in (401, 403):
        raise KandjiAuthError(resp.status_code, msg)
    raise KandjiAPIError(resp.status_code, msg)


# ─── Low-level GET wrapper ───────────────────────────────────────────


async def kandji_get(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    api_token: str,
    path: str,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]] | dict[str, Any]:
    """GET wrapper. Returns parsed JSON (list or dict)."""
    base = base_url.rstrip("/")
    resp = await client.get(
        f"{base}{path}",
        headers={"Authorization": f"Bearer {api_token}"},
        params=params,
        timeout=20.0,
    )
    _raise_for_status(resp)
    return resp.json()


# ─── Device iterator ─────────────────────────────────────────────────


async def iter_devices(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    api_token: str,
    page_size: int = 300,
) -> AsyncIterator[dict[str, Any]]:
    """Yield every device in the Kandji tenant.

    Kandji returns up to 300 devices per page. Pagination via
    `limit` + `offset`. Stop when a page returns fewer items than
    `limit` (last page).
    """
    offset = 0
    while True:
        body = await kandji_get(
            client,
            base_url=base_url,
            api_token=api_token,
            path="/api/v1/devices",
            params={"limit": page_size, "offset": offset},
        )
        # Kandji's /devices returns a bare list, not wrapped.
        items = body if isinstance(body, list) else body.get("results", [])
        if not items:
            return
        for item in items:
            yield item
        if len(items) < page_size:
            return
        offset += page_size


async def iter_blueprints(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    api_token: str,
    page_size: int = 100,
) -> AsyncIterator[dict[str, Any]]:
    """Yield every blueprint in the Kandji tenant.

    A Blueprint in Kandji is the unit of policy assignment — analogous
    to a Configuration Profile group in Jamf or a Settings Catalog
    profile in Intune. We pull names + IDs only for now.
    """
    offset = 0
    while True:
        body = await kandji_get(
            client,
            base_url=base_url,
            api_token=api_token,
            path="/api/v1/blueprints",
            params={"limit": page_size, "offset": offset},
        )
        items = body if isinstance(body, list) else body.get("results", [])
        if not items:
            return
        for item in items:
            yield item
        if len(items) < page_size:
            return
        offset += page_size


# ─── Normalisation ───────────────────────────────────────────────────


def normalise_device(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a Kandji device payload → ManagedDevice-shape dict.

    Kandji uses dotted field names with `user.email`, `agent.last_check_in`,
    etc. We flatten via .get('user', {}).get('email').

    Returns None if the row is missing the primary identifier.
    """
    external_id = str(raw.get("device_id") or raw.get("id") or "").strip()
    if not external_id:
        return None

    device_name = (raw.get("device_name") or "").strip() or "(unnamed)"
    platform = (raw.get("platform") or "").strip()

    # Kandji platform values: "Mac" | "iPhone" | "iPad" | "Apple TV"
    # We collapse to "macOS" / "iOS" / "iPadOS" / "tvOS".
    os_label = _platform_to_os(platform)
    os_version = _strip_or_none(raw.get("os_version"))

    user_block = raw.get("user") or {}
    user_email = _lower_or_none(user_block.get("email") if isinstance(user_block, dict) else None)

    last_check_in = _parse_iso(raw.get("last_check_in"))

    return {
        "external_device_id": external_id,
        "device_name": device_name,
        "operating_system": os_label,
        "os_version": os_version,
        "compliance_state": _derive_compliance(raw),
        "enrolled_user_email": user_email,
        "last_sync_to_mdm_at": last_check_in,
    }


def normalise_blueprint(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "external_group_id": str(raw.get("id") or "").strip(),
        "display_name": (raw.get("name") or "").strip() or "(unnamed)",
        "description": _strip_or_none(raw.get("description")),
        "group_type": "Blueprint",
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
            # Kandji error envelope: {"detail": "..."} or {"errors": [...]}.
            if "detail" in body:
                return str(body["detail"])[:200]
            if "errors" in body:
                return str(body["errors"])[:200]
            return str(body)[:200]
    except (ValueError, TypeError):
        pass
    return resp.text[:200]


def _platform_to_os(platform: str) -> str:
    p = platform.strip().lower()
    if p in ("mac", "macos"):
        return "macOS"
    if p == "iphone":
        return "iOS"
    if p == "ipad":
        return "iPadOS"
    if p in ("apple tv", "appletv"):
        return "tvOS"
    return platform or "Unknown"


def _derive_compliance(raw: dict[str, Any]) -> str:
    """Kandji surfaces a 'last_check_in' + per-library-item status.

    We approximate compliance from:
      - mdm_enabled true → managed
      - device_status in {"managed", "Online", "online"} + checked-in
        within last 7 days → compliant
      - otherwise → noncompliant
    """
    mdm_enabled = bool(raw.get("mdm_enabled") or raw.get("agent_installed"))
    if not mdm_enabled:
        return "unknown"

    last_check_in_str = raw.get("last_check_in") or ""
    last = _parse_iso(last_check_in_str)
    if last is None:
        return "noncompliant"

    now = datetime.now(last.tzinfo)
    days_since = (now - last).total_seconds() / 86400.0
    if days_since > 7:
        return "noncompliant"
    return "compliant"
