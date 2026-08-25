"""FastAPI application entry point."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app.adapters.defender  # noqa: F401

# Register adapters (triggers registration in the adapter registry)
import app.adapters.manual  # noqa: F401
import app.adapters.purview  # noqa: F401
from app.config import get_settings
from app.database import close_db, get_standalone_session, init_db
from app.errors import register_exception_handlers
from app.middleware.audit import AuditMiddleware
from app.middleware.correlation import CorrelationIdMiddleware
from app.middleware.tenant_context import TenantContextMiddleware
from app.observability import instrument_fastapi, setup_logging, setup_telemetry
from app.routers import (
    adapters,
    admin,
    adoption,
    agent_control,
    agent_discovery,
    ai_assets,
    ai_inventory,
    ardoq_export,
    auth,
    billing,
    compliance,
    correlations,
    cost,
    defender_import,
    developer,
    devices,
    dispatch,
    endpoints,
    extension,
    handbook,
    integrations,
    invites,
    mcp_discovery,
    mdm_connect,
    model_risk,
    monitoring,
    onboarding,
    pep,
    pii,
    risks,
    saas_vendor,
    sentinel_connect,
    signup,
    slack,
    surveys,
    teams,
    telemetry,
    tenants,
    use_case_reviews,
    use_cases,
    users,
    voice_interview,
)
from app.routers import (
    drift_signals as drift_signals_router,
)
from app.routers.billing import webhooks_router as billing_webhooks_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan — startup and shutdown hooks."""
    settings = get_settings()
    setup_logging(settings)
    setup_telemetry(settings)

    # Initialize database
    await init_db()

    # Bootstrap super admin
    if settings.super_admin_email and settings.super_admin_password:
        from app.services.auth_service import bootstrap_super_admin

        async with get_standalone_session() as db:
            await bootstrap_super_admin(
                db, settings.super_admin_email, settings.super_admin_password
            )

    import structlog

    logger = structlog.get_logger()
    logger.info("app_started", version=settings.app_version, env=settings.app_env.value)

    yield

    from app.auth.rate_limit import close_rate_limiter
    from app.auth.token_store import close_token_store

    await close_rate_limiter()
    await close_token_store()
    await close_db()
    logger.info("app_shutdown")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="AI-GRC Platform API",
        description="AI-powered Governance, Risk, and Compliance platform",
        version=settings.app_version,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        openapi_url="/openapi.json" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # ── Middleware (order matters: outermost first) ────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Correlation-ID"],
        expose_headers=["X-Correlation-ID"],
    )
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(TenantContextMiddleware)
    app.add_middleware(AuditMiddleware)

    # ── Exception handlers ────────────────────────────────────────────
    register_exception_handlers(app)

    # ── Routers ───────────────────────────────────────────────────────
    api_v1 = "/api/v1"
    app.include_router(signup.router, prefix=api_v1)
    app.include_router(auth.router, prefix=api_v1)
    app.include_router(tenants.router, prefix=api_v1)
    app.include_router(invites.router, prefix=api_v1)
    app.include_router(users.router, prefix=api_v1)
    app.include_router(teams.router, prefix=api_v1)
    app.include_router(adapters.router, prefix=api_v1)
    app.include_router(agent_discovery.router, prefix=api_v1)
    app.include_router(agent_control.router, prefix=api_v1)
    app.include_router(risks.router, prefix=api_v1)
    app.include_router(correlations.router, prefix=api_v1)
    app.include_router(dispatch.router, prefix=api_v1)
    app.include_router(monitoring.router, prefix=api_v1)
    app.include_router(use_cases.router, prefix=api_v1)
    app.include_router(ai_inventory.router, prefix=api_v1)
    app.include_router(saas_vendor.router, prefix=api_v1)
    app.include_router(ai_assets.router, prefix=api_v1)
    app.include_router(compliance.router, prefix=api_v1)
    app.include_router(model_risk.router, prefix=api_v1)
    app.include_router(surveys.router, prefix=api_v1)
    # Slack's /integrations/slack/* must register before the generic
    # integrations router so its specific paths win path resolution.
    # Same for the MDM connect endpoints — they live at
    # /integrations/{jamf,kandji,jumpcloud}/connect and need to win
    # over the generic /integrations/{provider}/install path.
    app.include_router(slack.integrations_router, prefix=api_v1)
    app.include_router(slack.slack_router, prefix=api_v1)
    app.include_router(mdm_connect.router, prefix=api_v1)
    app.include_router(sentinel_connect.router, prefix=api_v1)
    app.include_router(endpoints.router, prefix=api_v1)
    app.include_router(handbook.router, prefix=api_v1)
    app.include_router(use_case_reviews.router, prefix=api_v1)
    app.include_router(drift_signals_router.router, prefix=api_v1)
    app.include_router(extension.router, prefix=api_v1)
    app.include_router(devices.router, prefix=api_v1)
    app.include_router(ardoq_export.router, prefix=api_v1)
    app.include_router(integrations.router, prefix=api_v1)
    app.include_router(onboarding.router, prefix=api_v1)
    app.include_router(pep.router, prefix=api_v1)
    app.include_router(mcp_discovery.router, prefix=api_v1)
    app.include_router(voice_interview.router, prefix=api_v1)
    app.include_router(defender_import.router, prefix=api_v1)
    app.include_router(pii.router, prefix=api_v1)
    app.include_router(adoption.router, prefix=api_v1)
    app.include_router(admin.router, prefix=api_v1)
    app.include_router(developer.router, prefix=api_v1)
    app.include_router(telemetry.router, prefix=api_v1)
    app.include_router(cost.router, prefix=api_v1)
    app.include_router(billing.router, prefix=api_v1)
    # Webhook receiver is a SEPARATE router: /api/v1/webhooks/stripe (no JWT)
    app.include_router(billing_webhooks_router, prefix=api_v1)

    # ── Health check ──────────────────────────────────────────────────
    @app.get("/health", tags=["Health"])
    async def health() -> dict:
        result: dict = {"status": "healthy"}
        if not settings.is_production:
            result["version"] = settings.app_version
            result["environment"] = settings.app_env.value
        return result

    # ── OpenTelemetry instrumentation ─────────────────────────────────
    instrument_fastapi(app)

    return app


app = create_app()
