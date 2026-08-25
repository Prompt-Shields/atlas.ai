"""Integration management router — /api/v1/integrations.

Endpoints:

  GET    /integrations                        list every provider + tenant status
  GET    /integrations/{integration_id}       detail for a connected row
  PATCH  /integrations/{integration_id}       update display_name / config / is_active
  DELETE /integrations/{integration_id}       soft disconnect (preserves config)

  GET    /integrations/{provider}/install     returns the authorize URL
  GET    /integrations/microsoft/callback     OAuth callback (all MS providers)

Microsoft providers share a single callback path; the state payload
carries which provider was being connected. Slack has its own
callback at /integrations/slack/callback (PR #30).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthUser, OrgAdmin
from app.config import get_settings
from app.database import get_db_session
from app.errors import ConflictError, ForbiddenError, NotFoundError, UnauthorizedError
from app.models.integration import (
    Integration,
    IntegrationProvider,
    IntegrationStatus,
)
from app.models.slack_workspace import SlackWorkspace
from app.schemas.integration import (
    InstallStartResponse,
    IntegrationCard,
    IntegrationDisconnectResponse,
    IntegrationListResponse,
    IntegrationUpdate,
    ProviderMetaPayload,
)
from app.services.crypto import encrypt_token
from app.services.integration_registry import (
    get_provider,
    list_providers,
)
from app.services.microsoft_oauth import (
    MicrosoftOAuthError,
    MicrosoftOAuthStateError,
    parse_tenant_id_from_id_token,
)
from app.services.microsoft_oauth import (
    build_authorize_url as ms_build_authorize_url,
)
from app.services.microsoft_oauth import (
    decode_state as ms_decode_state,
)
from app.services.microsoft_oauth import (
    encode_state as ms_encode_state,
)
from app.services.microsoft_oauth import (
    exchange_code as ms_exchange_code,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/integrations", tags=["Integrations"])


# ─── Helpers ─────────────────────────────────────────────────────────


def _meta_payload(meta: Any) -> ProviderMetaPayload:
    return ProviderMetaPayload(
        provider=meta.provider,
        display_name=meta.display_name,
        short_name=meta.short_name,
        category=meta.category,
        vendor=meta.vendor,
        logo_slug=meta.logo_slug,
        description=meta.description,
        capabilities=meta.capabilities,
        available=meta.available,
        onboarding_recommended=meta.onboarding_recommended,
    )


def _scopes_list(scopes: str | None) -> list[str]:
    if not scopes:
        return []
    return (
        [s.strip() for s in scopes.split(",") if s.strip()]
        if "," in scopes
        else [s.strip() for s in scopes.split(" ") if s.strip()]
    )


def _safe_json_object(raw: str | None) -> dict[str, Any]:
    """Decode `config_json` Text → dict; empty dict on malformed input."""
    import json

    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _to_card(
    meta: Any,
    integration: Integration | None,
    slack_workspace: SlackWorkspace | None = None,
) -> IntegrationCard:
    """Build an IntegrationCard from static meta + optional live row.

    Slack is special-cased: its connected state lives in SlackWorkspace,
    not Integration. When listing, we surface it as Connected if a
    SlackWorkspace row exists.
    """
    if integration is not None:
        return IntegrationCard(
            meta=_meta_payload(meta),
            status=integration.status,
            integration_id=str(integration.id),
            display_name=integration.display_name,
            external_id=integration.external_id,
            external_name=integration.external_name,
            scopes=_scopes_list(integration.scopes),
            last_synced_at=integration.last_synced_at,
            last_error=integration.last_error,
            connected_at=integration.connected_at,
            config=_safe_json_object(integration.config_json),
        )

    # No Integration row. Two sub-cases:
    if meta.provider == IntegrationProvider.SLACK and slack_workspace is not None:
        return IntegrationCard(
            meta=_meta_payload(meta),
            status=IntegrationStatus.CONNECTED,
            integration_id=None,
            display_name=slack_workspace.team_name or meta.display_name,
            external_id=slack_workspace.team_id,
            external_name=slack_workspace.team_name,
            scopes=_scopes_list(slack_workspace.scopes),
            last_synced_at=None,
            last_error=None,
            connected_at=slack_workspace.installed_at,
            config={},
        )

    return IntegrationCard(
        meta=_meta_payload(meta),
        status=(
            IntegrationStatus.COMING_SOON if not meta.available else IntegrationStatus.NOT_CONNECTED
        ),
        integration_id=None,
        display_name=meta.display_name,
        external_id=None,
        external_name=None,
        scopes=[],
        last_synced_at=None,
        last_error=None,
        connected_at=None,
        config={},
    )


# ─── List ────────────────────────────────────────────────────────────


@router.get("", response_model=IntegrationListResponse)
async def list_integrations(
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> IntegrationListResponse:
    """List every provider + the caller's tenant's status.

    Always returns every entry in the static registry — disconnected
    or placeholder providers appear too, so the frontend grid is
    stable. Tenant-scoped: a Tenant A admin never sees Tenant B rows.
    """
    integrations_by_provider: dict[IntegrationProvider, Integration] = {}
    if user.tenant_id is not None or user.is_super_admin():
        query = select(Integration)
        if not user.is_super_admin():
            query = query.where(Integration.tenant_id == user.tenant_id)
        rows = list((await db.execute(query)).scalars().all())
        for row in rows:
            integrations_by_provider[row.provider] = row

    # Slack lives in its own table (PR #30).
    slack_workspace: SlackWorkspace | None = None
    if user.tenant_id is not None:
        slack_result = await db.execute(
            select(SlackWorkspace).where(
                SlackWorkspace.tenant_id == user.tenant_id,
                SlackWorkspace.is_active.is_(True),
            )
        )
        slack_workspace = slack_result.scalar_one_or_none()

    cards: list[IntegrationCard] = []
    for meta in list_providers():
        integration = integrations_by_provider.get(meta.provider)
        cards.append(_to_card(meta, integration, slack_workspace))

    return IntegrationListResponse(integrations=cards, total=len(cards))


# ─── Detail ──────────────────────────────────────────────────────────


@router.get("/{integration_id}", response_model=IntegrationCard)
async def get_integration(
    user: AuthUser,
    integration_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db_session),
) -> IntegrationCard:
    record = (
        await db.execute(select(Integration).where(Integration.id == integration_id))
    ).scalar_one_or_none()
    if record is None:
        raise NotFoundError("Integration", str(integration_id))
    if not user.is_super_admin() and record.tenant_id != user.tenant_id:
        raise NotFoundError("Integration", str(integration_id))
    meta = get_provider(record.provider)
    return _to_card(meta, record)


# ─── Update ──────────────────────────────────────────────────────────


@router.patch("/{integration_id}", response_model=IntegrationCard)
async def update_integration(
    payload: IntegrationUpdate,
    user: OrgAdmin,
    integration_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db_session),
) -> IntegrationCard:
    record = (
        await db.execute(select(Integration).where(Integration.id == integration_id))
    ).scalar_one_or_none()
    if record is None:
        raise NotFoundError("Integration", str(integration_id))
    if not user.is_super_admin() and record.tenant_id != user.tenant_id:
        raise NotFoundError("Integration", str(integration_id))

    provided = payload.model_dump(exclude_unset=True)
    if provided.get("display_name"):
        record.display_name = provided["display_name"].strip()
    if "config_json" in provided:
        record.config_json = json.dumps(provided["config_json"] or {})
    if "is_active" in provided:
        record.is_active = bool(provided["is_active"])
        if not record.is_active and record.status == IntegrationStatus.CONNECTED:
            record.status = IntegrationStatus.DISABLED
        elif record.is_active and record.status == IntegrationStatus.DISABLED:
            record.status = IntegrationStatus.CONNECTED

    await db.commit()
    await db.refresh(record)
    return _to_card(get_provider(record.provider), record)


# ─── Soft disconnect ─────────────────────────────────────────────────


@router.delete(
    "/{integration_id}",
    response_model=IntegrationDisconnectResponse,
)
async def disconnect_integration(
    user: OrgAdmin,
    integration_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db_session),
) -> IntegrationDisconnectResponse:
    """Soft disconnect — flips status to DISABLED, clears tokens.

    Config + audit metadata preserved. Re-connecting walks the OAuth
    flow again. Hard delete is not exposed.
    """
    record = (
        await db.execute(select(Integration).where(Integration.id == integration_id))
    ).scalar_one_or_none()
    if record is None:
        raise NotFoundError("Integration", str(integration_id))
    if not user.is_super_admin() and record.tenant_id != user.tenant_id:
        raise NotFoundError("Integration", str(integration_id))

    record.access_token_encrypted = None
    record.refresh_token_encrypted = None
    record.access_token_expires_at = None
    record.scopes = None
    record.status = IntegrationStatus.DISABLED
    record.is_active = False
    await db.commit()

    return IntegrationDisconnectResponse(
        integration_id=str(record.id),
        status=record.status,
    )


# ─── OAuth: install ──────────────────────────────────────────────────


@router.get(
    "/{provider}/install",
    response_model=InstallStartResponse,
)
async def install_integration(
    user: OrgAdmin,
    provider: IntegrationProvider = Path(...),
    db: AsyncSession = Depends(get_db_session),
) -> InstallStartResponse:
    """Return the authorize URL for the given provider.

    Microsoft providers all flow through `microsoft_oauth.build_authorize_url`.
    Slack uses `slack_oauth.build_authorize_url` (PR #30); for unification
    we surface its URL here too.

    The frontend opens this URL in a new tab (or redirects). The
    callback at the provider-specific path completes the install.
    """
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant — cannot install integrations")
    meta = get_provider(provider)
    if not meta.available:
        raise ConflictError(f"Provider {provider.value} is not yet available")

    settings = get_settings()

    # Microsoft path
    if meta.vendor == "microsoft":
        if (
            not settings.microsoft_client_id
            or not settings.microsoft_client_secret
            or not settings.microsoft_signing_secret
        ):
            raise ConflictError(
                "Microsoft integration not configured on this deployment "
                "— set MICROSOFT_CLIENT_ID / SECRET / SIGNING_SECRET"
            )
        state = ms_encode_state(
            signing_secret=settings.microsoft_signing_secret,
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            provider=provider,
            nonce=uuid.uuid4().hex,
        )
        url = ms_build_authorize_url(
            client_id=settings.microsoft_client_id,
            redirect_url=settings.microsoft_oauth_redirect_url,
            scopes=meta.scopes,
            state=state,
        )
        return InstallStartResponse(authorize_url=url, provider=provider)

    # Slack path — reuse the PR #30 helpers.
    if meta.vendor == "slack":
        from app.services.slack_oauth import (
            build_authorize_url as slack_build,
        )
        from app.services.slack_oauth import (
            encode_state as slack_encode,
        )

        if not settings.slack_client_id or not settings.slack_signing_secret:
            raise ConflictError("Slack integration not configured — set SLACK_CLIENT_ID etc.")
        state = slack_encode(
            signing_secret=settings.slack_signing_secret,
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            nonce=uuid.uuid4().hex,
        )
        url = slack_build(
            client_id=settings.slack_client_id,
            redirect_url=settings.slack_oauth_redirect_url,
            state=state,
        )
        return InstallStartResponse(authorize_url=url, provider=provider)

    # Should be unreachable: AWS/GCP have available=False which we
    # rejected above.
    raise ConflictError(f"No install flow for vendor {meta.vendor!r}")


# ─── OAuth: Microsoft callback ──────────────────────────────────────


@router.get("/microsoft/callback", response_class=HTMLResponse)
async def microsoft_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Single callback for all Microsoft providers.

    The state carries which IntegrationProvider was being connected;
    we look that up to fetch the right scope list, then upsert the
    Integration row with encrypted tokens.
    """
    settings = get_settings()
    if not settings.microsoft_signing_secret:
        raise ConflictError("Microsoft integration not configured")

    try:
        payload = ms_decode_state(signing_secret=settings.microsoft_signing_secret, state=state)
    except MicrosoftOAuthStateError as exc:
        raise UnauthorizedError(f"state invalid: {exc}")

    tenant_uuid = uuid.UUID(payload["tenant_id"])
    user_uuid = uuid.UUID(payload["user_id"])
    provider = IntegrationProvider(payload["provider"])
    meta = get_provider(provider)

    async with httpx.AsyncClient() as client:
        try:
            tokens = await ms_exchange_code(
                client=client,
                client_id=settings.microsoft_client_id,
                client_secret=settings.microsoft_client_secret,
                redirect_url=settings.microsoft_oauth_redirect_url,
                code=code,
                scopes=meta.scopes,
            )
        except MicrosoftOAuthError as exc:
            logger.warning("microsoft_exchange_failed", error=str(exc))
            raise UnauthorizedError(f"microsoft code exchange failed: {exc}")

    # Discover the Azure AD tenant id from the id_token (display only).
    aad_tenant = (
        parse_tenant_id_from_id_token(tokens["id_token"]) if tokens.get("id_token") else None
    )

    # Upsert.
    existing = (
        await db.execute(
            select(Integration).where(
                Integration.tenant_id == tenant_uuid,
                Integration.provider == provider,
            )
        )
    ).scalar_one_or_none()

    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=int(tokens["expires_in"]))
    access_ct = encrypt_token(tokens["access_token"])
    refresh_ct = encrypt_token(tokens["refresh_token"]) if tokens.get("refresh_token") else None
    granted_scopes = " ".join(tokens["scope"]) if tokens.get("scope") else None

    if existing is None:
        record = Integration(
            tenant_id=tenant_uuid,
            provider=provider,
            display_name=meta.display_name,
            external_id=aad_tenant,
            external_name=meta.display_name,
            access_token_encrypted=access_ct,
            refresh_token_encrypted=refresh_ct,
            access_token_expires_at=expires_at,
            scopes=granted_scopes,
            status=IntegrationStatus.CONNECTED,
            connected_by_user_id=user_uuid,
            connected_at=now,
            is_active=True,
        )
        db.add(record)
    else:
        existing.access_token_encrypted = access_ct
        existing.refresh_token_encrypted = refresh_ct
        existing.access_token_expires_at = expires_at
        existing.scopes = granted_scopes
        existing.external_id = aad_tenant or existing.external_id
        existing.status = IntegrationStatus.CONNECTED
        existing.is_active = True
        existing.connected_by_user_id = user_uuid
        existing.connected_at = now
        existing.last_error = None

    await db.commit()
    logger.info(
        "microsoft_integration_connected",
        tenant_id=str(tenant_uuid),
        provider=provider.value,
    )

    # Return a small HTML page that postMessages back to the
    # atlas window that opened this popup, then closes itself.
    # See app/services/oauth_callback_html.py.
    from app.services.oauth_callback_html import oauth_success_response

    return oauth_success_response(
        provider=provider.value,
        display_name=meta.display_name,
        extra_data={"aad_tenant": aad_tenant or ""},
    )
