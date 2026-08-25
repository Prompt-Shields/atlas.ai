"""Authentication router — login, token refresh, logout, API key management."""

from __future__ import annotations

import secrets
import uuid

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthUser, TenantAdmin
from app.auth.rate_limit import check_login_rate_limit
from app.auth.token_store import consume_sso_code, revoke_all_for_user, store_sso_code
from app.config import get_settings
from app.database import get_db_session
from app.errors import AppException, UnauthorizedError
from app.models.user import APIKey
from app.schemas.auth import (
    APIKeyCreateRequest,
    APIKeyCreateResponse,
    APIKeyResponse,
    LoginRequest,
    PasswordChangeRequest,
    RefreshRequest,
    SSOExchangeRequest,
    TokenResponse,
)
from app.services import auth_service, sso_login
from app.services.audit import log_audit_event
from app.services.microsoft_oauth import build_authorize_url

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> TokenResponse:
    """Authenticate and return a token pair. Rate limited per IP and email."""
    await check_login_rate_limit(request, body.email)

    result = await auth_service.login(db, email=body.email, password=body.password)

    await log_audit_event(
        db,
        event_type="auth.login",
        action="create",
        resource_type="session",
        details={"email": body.email},
        correlation_id=getattr(request.state, "correlation_id", None),
    )

    return TokenResponse(**result)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> TokenResponse:
    """Rotate token pair using a valid refresh token.

    Each refresh token is single-use. Reuse of a consumed token triggers
    full session revocation (replay-attack protection).
    """
    from app.auth.rate_limit import check_rate_limit

    client_ip = request.client.host if request.client else "unknown"
    await check_rate_limit(f"refresh:{client_ip}", max_attempts=20, window_seconds=60)

    result = await auth_service.refresh_tokens(db, refresh_token=body.refresh_token)
    return TokenResponse(**result)


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> None:
    """Revoke all refresh tokens for the current user (logout everywhere)."""
    await revoke_all_for_user(user.user_id)

    await log_audit_event(
        db,
        event_type="auth.logout",
        action="delete",
        actor_id=user.user_id,
        actor_email=user.email,
        resource_type="session",
        correlation_id=getattr(request.state, "correlation_id", None),
    )


@router.post("/password", status_code=204)
async def change_password(
    body: PasswordChangeRequest,
    request: Request,
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> None:
    """Change the current user's password. Revokes all sessions afterwards."""
    from app.auth.passwords import hash_password, verify_password
    from app.auth.rate_limit import check_rate_limit
    from app.models.user import User

    client_ip = request.client.host if request.client else "unknown"
    await check_rate_limit(f"password_change:{client_ip}", max_attempts=5, window_seconds=300)

    result = await db.execute(select(User).where(User.id == user.user_id))
    db_user = result.scalar_one_or_none()
    if not db_user:
        from app.errors import NotFoundError

        raise NotFoundError("User")

    if not verify_password(body.current_password, db_user.hashed_password):
        from app.errors import UnauthorizedError

        raise UnauthorizedError("Current password is incorrect")

    db_user.hashed_password = hash_password(body.new_password)
    await db.flush()

    await revoke_all_for_user(user.user_id)

    await log_audit_event(
        db,
        event_type="auth.password_changed",
        action="update",
        actor_id=user.user_id,
        actor_email=user.email,
        resource_type="user",
        resource_id=str(user.user_id),
        correlation_id=getattr(request.state, "correlation_id", None),
    )


@router.post("/api-keys", response_model=APIKeyCreateResponse, status_code=201)
async def create_api_key(
    body: APIKeyCreateRequest,
    request: Request,
    user: TenantAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> APIKeyCreateResponse:
    """Create an API key scoped to the user's tenant."""
    raw_key, api_key = await auth_service.create_api_key_for_user(
        db,
        user_id=user.user_id,
        name=body.name,
        description=body.description,
        tenant_id=user.tenant_id,
    )

    await log_audit_event(
        db,
        event_type="api_key.created",
        action="create",
        actor_id=user.user_id,
        actor_email=user.email,
        tenant_id=user.tenant_id,
        resource_type="api_key",
        resource_id=str(api_key.id),
        details={"key_prefix": api_key.key_prefix, "name": body.name},
        correlation_id=getattr(request.state, "correlation_id", None),
    )

    return APIKeyCreateResponse(
        id=str(api_key.id),
        key=raw_key,
        key_prefix=api_key.key_prefix,
        name=api_key.name,
        description=api_key.description,
    )


@router.get("/api-keys", response_model=list[APIKeyResponse])
async def list_api_keys(
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> list[APIKeyResponse]:
    """List the current user's API keys (secrets are never returned)."""
    result = await db.execute(
        select(APIKey).where(APIKey.user_id == user.user_id).order_by(APIKey.created_at.desc())
    )
    keys = list(result.scalars().all())
    return [
        APIKeyResponse(
            id=str(k.id),
            key_prefix=k.key_prefix,
            name=k.name,
            description=k.description,
            is_active=k.is_active,
            created_at=str(k.created_at),
        )
        for k in keys
    ]


@router.delete("/api-keys/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: str,
    request: Request,
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> None:
    """Revoke (deactivate) an API key. Only the owner or a super admin can revoke."""
    kid = uuid.UUID(key_id)
    result = await db.execute(select(APIKey).where(APIKey.id == kid))
    api_key = result.scalar_one_or_none()
    if not api_key:
        from app.errors import NotFoundError

        raise NotFoundError("API Key", key_id)

    if api_key.user_id != user.user_id and not user.is_super_admin():
        from app.errors import ForbiddenError

        raise ForbiddenError("You can only revoke your own API keys")

    api_key.is_active = False
    await db.flush()

    await log_audit_event(
        db,
        event_type="api_key.revoked",
        action="delete",
        actor_id=user.user_id,
        actor_email=user.email,
        tenant_id=user.tenant_id,
        resource_type="api_key",
        resource_id=key_id,
        correlation_id=getattr(request.state, "correlation_id", None),
    )


# ─── Entra ID SSO login (PRO-55) ─────────────────────────────────────


def _require_microsoft_configured() -> None:
    settings = get_settings()
    if not (
        settings.microsoft_client_id
        and settings.microsoft_client_secret
        and settings.microsoft_signing_secret
    ):
        raise AppException(
            code="SSO_NOT_CONFIGURED",
            message=(
                "Microsoft SSO is not configured on this deployment "
                "— set MICROSOFT_CLIENT_ID / SECRET / SIGNING_SECRET"
            ),
            status_code=503,
        )


@router.get("/sso/microsoft/login")
async def sso_microsoft_login() -> RedirectResponse:
    """Begin the Entra ID auth-code flow — redirect to Microsoft.

    The OIDC `nonce` is carried in the HMAC-signed `state` and as the authorize
    `nonce` param; the callback re-binds them, so no server-side session store.
    """
    _require_microsoft_configured()
    settings = get_settings()
    nonce = uuid.uuid4().hex
    state = sso_login.encode_login_state(
        signing_secret=settings.microsoft_signing_secret, nonce=nonce
    )
    url = build_authorize_url(
        client_id=settings.microsoft_client_id,
        redirect_url=settings.microsoft_sso_redirect_url,
        scopes=sso_login.LOGIN_SCOPES,
        state=state,
        nonce=nonce,
    )
    return RedirectResponse(url, status_code=307)


@router.get("/sso/microsoft/callback")
async def sso_microsoft_callback(
    code: str,
    state: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    """Complete SSO login, then hand off to the SPA via a one-time code.

    On success: mint a short-lived single-use code, store the issued token pair
    against it, and redirect the browser to the SPA callback with the code (no
    tokens in the URL). On failure: redirect to the login page with an
    `sso_error` so the user sees a friendly message rather than raw JSON.
    """
    _require_microsoft_configured()
    settings = get_settings()
    login_url = f"{settings.frontend_base_url}/login"
    try:
        async with httpx.AsyncClient() as client:
            result = await sso_login.complete_sso_login(
                db,
                code=code,
                state=state,
                settings=settings,
                client=client,
            )
    except sso_login.SSOAccountNotProvisionedError:
        return RedirectResponse(f"{login_url}?sso_error=not_provisioned", status_code=307)
    except (sso_login.SSOIdTokenError, sso_login.SSOLoginStateError):
        return RedirectResponse(f"{login_url}?sso_error=invalid", status_code=307)

    await db.commit()
    await log_audit_event(
        db,
        event_type="auth.sso_login",
        action="create",
        resource_type="session",
        details={"provider": "microsoft_entra_id"},
        correlation_id=getattr(request.state, "correlation_id", None),
    )

    handoff_code = secrets.token_urlsafe(32)
    await store_sso_code(handoff_code, result)
    return RedirectResponse(
        f"{settings.frontend_base_url}/auth/sso/callback?code={handoff_code}",
        status_code=307,
    )


@router.post("/sso/exchange", response_model=TokenResponse)
async def sso_exchange(body: SSOExchangeRequest) -> TokenResponse:
    """Exchange a one-time SSO handoff code for the token pair (single-use)."""
    tokens = await consume_sso_code(body.code)
    if tokens is None:
        raise UnauthorizedError("Invalid or expired SSO code")
    return TokenResponse(**tokens)
