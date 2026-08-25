"""Application configuration via pydantic-settings.

All secrets come from environment variables. In Azure these are Key Vault
references injected into Container Apps. Locally they come from .env files.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(str, Enum):
    DEVELOPMENT = "development"
    TESTING = "testing"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ───────────────────────────────────────────────────────────
    app_env: Environment = Environment.DEVELOPMENT
    app_debug: bool = False
    app_version: str = "0.1.0"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # ── Database ──────────────────────────────────────────────────────
    database_url: str = Field(..., description="Async SQLAlchemy connection string")
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # ── Redis ─────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── Auth ──────────────────────────────────────────────────────────
    jwt_secret_key: str = Field(..., min_length=16)
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 15
    jwt_refresh_token_expire_days: int = 7
    api_key_hash_rounds: int = 12

    # ── Email ─────────────────────────────────────────────────────────
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "noreply@aigrc.local"
    email_backend: Literal["smtp", "console"] = "console"

    # ── Azure OpenAI ──────────────────────────────────────────────────
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_deployment_name: str = "gpt-4o"
    azure_openai_api_version: str = "2024-12-01-preview"

    # ── Observability ─────────────────────────────────────────────────
    applicationinsights_connection_string: str = ""
    otel_service_name: str = "aigrc-backend"

    # ── CORS / APIM ──────────────────────────────────────────────────
    backend_cors_origins: str = "http://localhost:3000"

    # ── Worker ────────────────────────────────────────────────────────
    worker_batch_size: int = 100
    worker_poll_interval_seconds: int = 30

    # ── Super Admin Bootstrap ─────────────────────────────────────────
    super_admin_email: str = "admin@aigrc.local"
    super_admin_password: str = Field(default="", min_length=0)

    # ── Slack integration (survey bot) ────────────────────────────────
    # Per `docs/use-case-survey-bot.md`. App is private-per-tenant in
    # v0.1; Slack marketplace listing is v0.2.
    slack_client_id: str = ""
    slack_client_secret: str = ""
    slack_signing_secret: str = ""
    # Fernet key (URL-safe base64, 32 bytes). Generate via
    #   python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
    slack_token_encryption_key: str = ""
    slack_token_encryption_key_old: str = ""  # optional, for rotation overlap
    slack_oauth_redirect_url: str = "http://localhost:8000/api/v1/integrations/slack/callback"

    # ── Microsoft integrations (Entra ID / Intune / Purview / Defender) ──
    # All share a single Azure AD multi-tenant app. Signing secret is
    # reused for the OAuth `state` HMAC across all Microsoft providers.
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_signing_secret: str = ""
    microsoft_oauth_redirect_url: str = (
        "http://localhost:8000/api/v1/integrations/microsoft/callback"
    )
    # SSO login (PRO-55) uses a distinct callback path from the data-integration
    # OAuth flow above, so a returning login code is never confused with an
    # integration-connect code.
    microsoft_sso_redirect_url: str = "http://localhost:8000/api/v1/auth/sso/microsoft/callback"
    # Where the SSO callback redirects the browser after minting a one-time code
    # (PRO-55 handoff). The SPA exchanges the code for tokens at /auth/sso/exchange.
    frontend_base_url: str = "http://localhost:3000"

    # ── Cost ledger cron ──────────────────────────────────────────────
    # Shared secret guarding the all-tenant cost sync endpoint
    # (POST /api/v1/cost/sync). Compared against the X-Cron-Secret header
    # via hmac.compare_digest. When unset, the endpoint returns 503.
    cost_sync_cron_secret: str | None = None

    # ── PEP auto-demote watchdog cron ───────────────────────────────────
    # Shared secret guarding the all-tenant PEP watchdog sweep
    # (POST /api/v1/pep/watchdog/tick). Same X-Cron-Secret convention as
    # cost_sync_cron_secret above.
    pep_watchdog_cron_secret: str | None = None

    # ── Stripe billing ────────────────────────────────────────────────
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_team_monthly: str = ""
    stripe_price_team_annual: str = ""
    billing_portal_return_url: str = "http://localhost:3000/dashboard/billing"
    billing_checkout_success_url: str = "http://localhost:3000/dashboard/billing?upgraded=1"
    billing_checkout_cancel_url: str = "http://localhost:3000/dashboard/billing"

    # ── Feature Flags ─────────────────────────────────────────────────
    research_all_data: bool = False

    # Entra ID SSO self-serve sign-up (SP-Azure). When False (default), SSO is
    # login-only: the callback rejects Microsoft identities with no linked
    # platform user (SSOAccountNotProvisioned). When True, an unlinked identity
    # JIT-provisions a tenant (first user of an Azure `tid`) + 14-day trial, or
    # joins the existing tenant for that `tid` as a member.
    sso_self_serve_signup: bool = False

    # Agent discovery (#233): real SDK-backed collectors (Bedrock AgentCore,
    # Azure AI Foundry, GCP Knowledge Catalog) are not implemented yet, so a
    # scan always uses seeded fixtures regardless of this flag until they land.
    agent_discovery_live_collectors_enabled: bool = False

    @field_validator("backend_cors_origins")
    @classmethod
    def parse_cors(cls, v: str) -> str:
        return v

    @model_validator(mode="after")
    def _enforce_production_security(self) -> Settings:
        if self.app_env in (Environment.PRODUCTION, Environment.STAGING):
            if len(self.jwt_secret_key) < 32:
                raise ValueError(
                    "JWT_SECRET_KEY must be at least 32 characters in production/staging"
                )
            if self.app_debug:
                raise ValueError("APP_DEBUG must be false in production/staging")
            origins = [o.strip() for o in self.backend_cors_origins.split(",") if o.strip()]
            for origin in origins:
                if origin.startswith("http://") and "localhost" not in origin:
                    raise ValueError(
                        f"Non-localhost HTTP CORS origin '{origin}' is not allowed in production/staging. Use HTTPS."
                    )
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.backend_cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == Environment.PRODUCTION

    @property
    def is_testing(self) -> bool:
        return self.app_env == Environment.TESTING


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
