"""Dedicated connect endpoints for the pull-mode AI cost providers.

The cost ledger has two halves. Push-mode providers (self-hosted,
Azure AI Foundry, Bedrock) report their own spend to
``POST /cost/usage`` and need no stored credential. Pull-mode
providers — Anthropic, OpenAI, Cursor, GitHub Copilot, Vercel — are
the other half: their connectors in ``app/services/cost/`` call the
vendor's billing API on a schedule and need an admin credential on the
``Integration`` row to do it.

Until this module there was no way to put one there. The connectors
read ``Integration.access_token_encrypted``, but no endpoint wrote it:
``PATCH /integrations/{id}`` sets only ``display_name``, ``config_json``
and ``is_active``, and none of these providers have an OAuth flow. They
advertised ``available=True`` in the registry and could not be
connected. These endpoints close that gap.

**Verification runs the real sync path.** Each endpoint builds a
transient (unsaved) ``Integration`` carrying the submitted credential
and awaits the provider's own ``fetch_cost`` over a one-day window. A
credential that passes here is therefore known to work for the nightly
sync — not merely known to authenticate against some cheaper probe
endpoint that may carry different scopes. Copilot is the sharp case:
its token needs ``manage_billing:copilot`` on a *specific org*, so a
token that lists fine under ``GET /user`` can still be useless to us.

Storage shape, which differs from ``mdm_connect`` on purpose:

* ``access_token_encrypted`` holds the **bare credential**, Fernet
  encrypted — not a JSON blob. Every cost connector does a plain
  ``decrypt_token(...)`` and uses the result directly as the key.
* Non-secret provider settings (``github_org``, ``vercel_team_id``)
  go in ``config_json``, which ``GET /integrations`` returns to the
  browser.
* A *second* secret (Vercel's AI Gateway key) goes in ``config_json``
  as ciphertext under a ``_encrypted`` suffix, which
  ``integration_connect.redact_config`` strips on the way out.

Same tenant scoping as the rest of the integrations surface: OrgAdmin
connects for their own tenant, and never across tenants.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import httpx
import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import OrgAdmin
from app.database import get_db_session
from app.errors import ConflictError, ForbiddenError, UnauthorizedError
from app.models.integration import Integration, IntegrationProvider
from app.schemas.integration import IntegrationCard
from app.services.cost.anthropic_cost import AnthropicCostConnector
from app.services.cost.copilot_cost import CopilotCostConnector
from app.services.cost.cursor_cost import CursorCostConnector
from app.services.cost.openai_cost import OpenAICostConnector
from app.services.cost.vercel_cost import VercelCostConnector
from app.services.crypto import encrypt_token
from app.services.integration_connect import to_card, upsert_connected

logger = structlog.get_logger()
router = APIRouter(prefix="/integrations", tags=["Integrations"])

#: Shortest credential we will even attempt to verify. Every vendor
#: here issues keys far longer than this; the check exists to turn an
#: empty paste into a clear 422 instead of a confusing vendor 401.
_MIN_KEY_LEN = 10


class _CostConnector(Protocol):
    """The slice of the cost-connector interface verification needs."""

    async def fetch_cost(self, integration: Integration, since: date, until: date) -> list[Any]: ...


# ─── Request schemas ─────────────────────────────────────────────────


class _KeyOnlyRequest(BaseModel):
    """Providers whose whole credential is a single admin key."""

    api_key: str = Field(
        ...,
        min_length=_MIN_KEY_LEN,
        description="Organization-level admin API key. Encrypted at rest.",
    )

    @field_validator("api_key")
    @classmethod
    def _strip(cls, v: str) -> str:
        # Copy-paste from a vendor console routinely carries whitespace.
        stripped = v.strip()
        if len(stripped) < _MIN_KEY_LEN:
            raise ValueError("API key looks too short — paste the full value")
        return stripped


class AnthropicConnectRequest(_KeyOnlyRequest):
    """Anthropic Admin API key (``sk-ant-admin…``)."""


class OpenAIConnectRequest(_KeyOnlyRequest):
    """OpenAI organization admin key (``sk-admin…``)."""


class CursorConnectRequest(_KeyOnlyRequest):
    """Cursor Teams Admin API key."""


class CopilotConnectRequest(_KeyOnlyRequest):
    """GitHub token plus the org whose Copilot seats we bill."""

    github_org: str = Field(
        ...,
        min_length=1,
        description="Org slug whose Copilot seats are billed, e.g. 'promptshields'.",
    )
    seat_price_usd: Decimal | None = Field(
        None,
        gt=0,
        description=(
            "Per-seat monthly price in USD. GitHub exposes no dollar figure "
            "over the API, so spend is derived as seats x price. Falls back "
            "to the connector's documented default when omitted."
        ),
    )

    @field_validator("github_org")
    @classmethod
    def _clean_org(cls, v: str) -> str:
        # Admins paste the org URL as often as the slug.
        slug = v.strip().rstrip("/").split("/")[-1]
        if not slug:
            raise ValueError("Organization slug is required")
        return slug


class VercelConnectRequest(_KeyOnlyRequest):
    """Vercel access token, team scoping, and optional AI Gateway."""

    team_id: str | None = Field(None, description="Vercel team id (team_…).")
    team_slug: str | None = Field(None, description="Vercel team slug.")
    ai_gateway: bool = Field(
        False,
        description="Also ingest AI Gateway model spend.",
    )
    ai_gateway_key: str | None = Field(
        None,
        description=(
            "Separate AI Gateway key. Optional — the main access token is "
            "used when omitted. Stored encrypted, never returned."
        ),
    )


# ─── Verification ────────────────────────────────────────────────────


def _probe(
    *,
    provider: IntegrationProvider,
    api_key: str,
    config: dict[str, Any],
) -> Integration:
    """An unsaved Integration standing in for the row we may persist.

    Never added to the session: if verification fails there must be no
    trace of the rejected credential in the database.
    """
    return Integration(
        provider=provider,
        access_token_encrypted=encrypt_token(api_key),
        config_json=json.dumps(config),
    )


async def _verify(
    connector: _CostConnector,
    probe: Integration,
    *,
    vendor: str,
) -> None:
    """Run one real ``fetch_cost`` and translate failures to HTTP errors.

    An empty result is a pass: a tenant with no spend yesterday is
    normal, and only an exception means the credential cannot be used.
    """
    until = datetime.now(UTC).date()
    since = until - timedelta(days=1)
    try:
        await connector.fetch_cost(probe, since, until)
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code in (401, 403):
            raise UnauthorizedError(
                f"{vendor} rejected the credential (HTTP {code}). Check the key "
                f"is an organization-level admin key with billing access."
            )
        if code == 404:
            raise ConflictError(
                f"{vendor} returned 404 — the organization or team in this "
                f"configuration does not exist, or the key cannot see it."
            )
        raise ConflictError(f"{vendor} API error (HTTP {code}).")
    except httpx.HTTPError as exc:
        raise ConflictError(f"Could not reach {vendor}: {exc}")
    except (ValueError, KeyError, InvalidOperation) as exc:
        # Malformed/unexpected payload — the credential may be valid but
        # the connector cannot use it, which is still a failed connect.
        raise ConflictError(f"{vendor} returned an unusable billing response: {exc}")


async def _connect(
    db: AsyncSession,
    user: OrgAdmin,
    *,
    provider: IntegrationProvider,
    connector: _CostConnector,
    vendor: str,
    api_key: str,
    display_name: str,
    external_id: str | None = None,
    config: dict[str, Any] | None = None,
) -> IntegrationCard:
    """Verify, then encrypt and persist. Shared by all five endpoints."""
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant")

    cfg = config or {}
    await _verify(connector, _probe(provider=provider, api_key=api_key, config=cfg), vendor=vendor)

    record = await upsert_connected(
        db,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        provider=provider,
        display_name=display_name,
        external_id=external_id,
        encrypted_blob=encrypt_token(api_key),
        config=cfg,
    )
    logger.info(
        "cost_provider_connected",
        provider=provider.value,
        tenant_id=str(user.tenant_id),
        integration_id=str(record.id),
    )
    return to_card(record)


# ─── Anthropic ───────────────────────────────────────────────────────


@router.post("/anthropic/connect", response_model=IntegrationCard)
async def anthropic_connect(
    payload: AnthropicConnectRequest,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> IntegrationCard:
    """Connect Anthropic org spend via an Admin API key."""
    return await _connect(
        db,
        user,
        provider=IntegrationProvider.ANTHROPIC,
        connector=AnthropicCostConnector(),
        vendor="Anthropic",
        api_key=payload.api_key,
        display_name="Anthropic",
    )


# ─── OpenAI ──────────────────────────────────────────────────────────


@router.post("/openai/connect", response_model=IntegrationCard)
async def openai_connect(
    payload: OpenAIConnectRequest,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> IntegrationCard:
    """Connect OpenAI API spend via an organization admin key."""
    return await _connect(
        db,
        user,
        provider=IntegrationProvider.OPENAI,
        connector=OpenAICostConnector(),
        vendor="OpenAI",
        api_key=payload.api_key,
        display_name="OpenAI",
    )


# ─── Cursor ──────────────────────────────────────────────────────────


@router.post("/cursor/connect", response_model=IntegrationCard)
async def cursor_connect(
    payload: CursorConnectRequest,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> IntegrationCard:
    """Connect Cursor per-member spend via a Teams Admin API key."""
    return await _connect(
        db,
        user,
        provider=IntegrationProvider.CURSOR,
        connector=CursorCostConnector(),
        vendor="Cursor",
        api_key=payload.api_key,
        display_name="Cursor",
    )


# ─── GitHub Copilot ──────────────────────────────────────────────────


@router.post("/github-copilot/connect", response_model=IntegrationCard)
async def github_copilot_connect(
    payload: CopilotConnectRequest,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> IntegrationCard:
    """Connect GitHub Copilot seat spend for one organization."""
    config: dict[str, Any] = {"github_org": payload.github_org}
    if payload.seat_price_usd is not None:
        # Stored as a string so the JSON round-trip cannot bring back a
        # binary float where the connector expects an exact Decimal.
        config["copilot_seat_price_usd"] = str(payload.seat_price_usd)

    return await _connect(
        db,
        user,
        provider=IntegrationProvider.GITHUB_COPILOT,
        connector=CopilotCostConnector(),
        vendor="GitHub",
        api_key=payload.api_key,
        display_name=f"GitHub Copilot ({payload.github_org})",
        external_id=payload.github_org,
        config=config,
    )


# ─── Vercel ──────────────────────────────────────────────────────────


@router.post("/vercel/connect", response_model=IntegrationCard)
async def vercel_connect(
    payload: VercelConnectRequest,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> IntegrationCard:
    """Connect Vercel platform spend, optionally with AI Gateway."""
    config: dict[str, Any] = {}
    if payload.team_id:
        config["vercel_team_id"] = payload.team_id.strip()
    if payload.team_slug:
        config["vercel_team_slug"] = payload.team_slug.strip()
    if payload.ai_gateway:
        config["vercel_ai_gateway"] = True
        if payload.ai_gateway_key:
            config["vercel_ai_gateway_key_encrypted"] = encrypt_token(
                payload.ai_gateway_key.strip()
            )

    scope = payload.team_slug or payload.team_id
    return await _connect(
        db,
        user,
        provider=IntegrationProvider.VERCEL,
        connector=VercelCostConnector(),
        vendor="Vercel",
        api_key=payload.api_key,
        display_name=f"Vercel ({scope})" if scope else "Vercel",
        external_id=payload.team_id or payload.team_slug,
        config=config,
    )
