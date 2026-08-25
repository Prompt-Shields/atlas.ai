"""Microsoft Graph v1.0 client — thin, mockable, httpx-based.

Used by:
  - `microsoft_sync` worker (this PR) — pulls users + groups
  - Future: Intune device list, Purview audit events, Defender CASB
    cloud-app feed

Returns plain dicts / lists straight from Graph's response payloads
— we don't model every Graph entity at this layer. The sync
orchestrator translates into ORM rows.

Pagination via the `@odata.nextLink` field — `iter_users` and
`iter_groups` transparently follow.

Error model:
  - HTTP 401 → GraphAuthExpired (caller should refresh the access
    token via microsoft_oauth.refresh_access_token and retry)
  - HTTP 429 → GraphRateLimited (caller should back off; Graph
    sends Retry-After header)
  - Other 4xx/5xx → GraphAPIError with the inner JSON error.code
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


# ─── Errors ──────────────────────────────────────────────────────────


class GraphAPIError(Exception):
    """Graph returned a non-2xx with a JSON error.code envelope."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        super().__init__(f"graph {status_code} {code}: {message}")


class GraphAuthExpired(GraphAPIError):
    """401 from Graph — caller should refresh the token + retry."""


class GraphRateLimited(GraphAPIError):
    """429 from Graph — caller should respect Retry-After."""

    def __init__(self, retry_after_seconds: int, message: str) -> None:
        super().__init__(429, "throttled", message)
        self.retry_after_seconds = retry_after_seconds


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.is_success:
        return
    try:
        body = resp.json()
        err = body.get("error", {}) if isinstance(body, dict) else {}
        code = err.get("code", "unknown")
        message = err.get("message", "")
    except (ValueError, TypeError):
        code = "unknown"
        message = resp.text[:200]

    if resp.status_code == 401:
        raise GraphAuthExpired(401, code, message)
    if resp.status_code == 429:
        retry_after = int(resp.headers.get("Retry-After", "60") or 60)
        raise GraphRateLimited(retry_after, message)
    raise GraphAPIError(resp.status_code, code, message)


# ─── Low-level helpers ───────────────────────────────────────────────


async def graph_get(
    client: httpx.AsyncClient,
    *,
    token: str,
    path: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """GET wrapper for a single Graph endpoint."""
    resp = await client.get(
        f"{GRAPH_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=20.0,
    )
    _raise_for_status(resp)
    return resp.json()


async def graph_get_url(
    client: httpx.AsyncClient,
    *,
    token: str,
    url: str,
) -> dict[str, Any]:
    """GET an absolute Graph URL (used to follow @odata.nextLink)."""
    resp = await client.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=20.0,
    )
    _raise_for_status(resp)
    return resp.json()


# ─── User iterator ───────────────────────────────────────────────────


USER_SELECT_FIELDS = "id,userPrincipalName,mail,displayName,department,jobTitle,accountEnabled"


async def iter_users(
    client: httpx.AsyncClient,
    *,
    token: str,
    page_size: int = 100,
) -> AsyncIterator[dict[str, Any]]:
    """Yield every user in the tenant.

    Uses `$select` to keep the payload small + ConsistencyLevel
    eventual so we can paginate at the requested `$top` size.
    """
    initial = await graph_get(
        client,
        token=token,
        path="/users",
        params={
            "$select": USER_SELECT_FIELDS,
            # Pull each user's manager id in the same page (no per-user call).
            "$expand": "manager($select=id)",
            "$top": page_size,
        },
    )
    async for user in _walk_pages(client, initial, token):
        yield user


async def iter_groups(
    client: httpx.AsyncClient,
    *,
    token: str,
    page_size: int = 100,
) -> AsyncIterator[dict[str, Any]]:
    """Yield every group in the tenant.

    Pulls the Graph fields we render (id, displayName, description,
    groupTypes). Doesn't yet pull memberships — that's a v0.2
    expansion.
    """
    initial = await graph_get(
        client,
        token=token,
        path="/groups",
        params={
            "$select": "id,displayName,description,groupTypes",
            "$top": page_size,
        },
    )
    async for group in _walk_pages(client, initial, token):
        yield group


async def iter_group_members(
    client: httpx.AsyncClient,
    *,
    token: str,
    group_id: str,
    page_size: int = 100,
) -> AsyncIterator[dict[str, Any]]:
    """Yield the *user* members of a group.

    Uses the `microsoft.graph.user` OData cast so non-user members
    (nested groups, service principals, devices) are filtered out
    server-side — we only model user↔group membership.
    """
    initial = await graph_get(
        client,
        token=token,
        path=f"/groups/{group_id}/members/microsoft.graph.user",
        params={
            "$select": "id",
            "$top": page_size,
        },
    )
    async for member in _walk_pages(client, initial, token):
        yield member


async def _walk_pages(
    client: httpx.AsyncClient,
    initial: dict[str, Any],
    token: str,
) -> AsyncIterator[dict[str, Any]]:
    page = initial
    while True:
        for item in page.get("value", []):
            yield item
        next_url = page.get("@odata.nextLink")
        if not next_url:
            return
        page = await graph_get_url(client, token=token, url=next_url)


# ─── Normalisation helpers ───────────────────────────────────────────


def normalise_user(raw: dict[str, Any]) -> dict[str, Any]:
    """Pull our DirectoryUser fields out of a Graph user payload."""
    email_source = raw.get("mail") or raw.get("userPrincipalName") or ""
    email = email_source.strip().lower() or None
    manager = raw.get("manager")
    manager_id = _strip_or_none(manager.get("id")) if isinstance(manager, dict) else None
    return {
        "external_user_id": str(raw.get("id") or "").strip(),
        "email": email,
        "display_name": (raw.get("displayName") or "").strip() or (email or ""),
        "department": _strip_or_none(raw.get("department")),
        "job_title": _strip_or_none(raw.get("jobTitle")),
        "manager_external_user_id": manager_id,
        "is_active": bool(raw.get("accountEnabled", True)),
    }


def normalise_group(raw: dict[str, Any]) -> dict[str, Any]:
    """Pull our DirectoryGroup fields out of a Graph group payload."""
    group_types = raw.get("groupTypes") or []
    if isinstance(group_types, list) and group_types:
        group_type = "Microsoft365" if "Unified" in group_types else "Distribution"
    else:
        # Security-only groups don't have a groupTypes entry.
        group_type = "Security"
    return {
        "external_group_id": str(raw.get("id") or "").strip(),
        "display_name": (raw.get("displayName") or "").strip() or "(unnamed)",
        "description": _strip_or_none(raw.get("description")),
        "group_type": group_type,
    }


def normalise_member(raw: dict[str, Any]) -> dict[str, Any]:
    """Pull the Graph user id out of a `/groups/{id}/members` payload."""
    return {"external_user_id": str(raw.get("id") or "").strip()}


def _strip_or_none(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
