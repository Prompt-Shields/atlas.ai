"""Integration schemas — list / detail / update.

The list endpoint returns a unified shape that the frontend grid
consumes: every provider in the registry appears, whether or not
a tenant has a row for it. Connected → IntegrationStatus.CONNECTED;
not-connected → NOT_CONNECTED; placeholder → COMING_SOON.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.integration import (
    IntegrationProvider,
    IntegrationStatus,
)


class ProviderMetaPayload(BaseModel):
    """Static provider metadata — mirror of services.integration_registry.ProviderMeta."""

    provider: IntegrationProvider
    display_name: str
    short_name: str
    category: Literal["identity", "device", "data", "communication", "cloud"]
    vendor: Literal["microsoft", "slack", "aws", "gcp", "other"]
    logo_slug: str
    description: str
    capabilities: list[str]
    available: bool
    onboarding_recommended: bool


class IntegrationCard(BaseModel):  # noqa: D101
    """One row in the /integrations grid.

    Combines static provider metadata with the live connection
    state for this tenant. The status field tells the frontend which
    card layout to render (Connect / Connected / Reconnect / Coming Soon).
    """

    meta: ProviderMetaPayload
    status: IntegrationStatus
    integration_id: str | None  # null if not yet connected
    display_name: str  # tenant-specific override or meta.display_name
    external_id: str | None
    external_name: str | None
    scopes: list[str]
    last_synced_at: datetime | None
    last_error: str | None
    connected_at: datetime | None
    # Tenant-specific provider config (e.g. Intune device-group filter,
    # Purview classifier picker). JSON object; shape varies per provider
    # and is interpreted by that provider's sync worker. Empty {} when
    # not configured.
    config: dict[str, Any] = Field(default_factory=dict)


class IntegrationListResponse(BaseModel):
    integrations: list[IntegrationCard]
    total: int


class IntegrationUpdate(BaseModel):
    """Admin can edit display_name, config_json, enable/disable.

    Tokens are never modified here — re-connecting goes through the
    OAuth flow.
    """

    display_name: str | None = Field(None, max_length=255)
    config_json: dict[str, Any] | None = None
    is_active: bool | None = None


class IntegrationDisconnectResponse(BaseModel):
    """Result of DELETE /integrations/{id} — soft disconnect."""

    integration_id: str
    status: IntegrationStatus


# ─── OAuth flow responses ────────────────────────────────────────────


class InstallStartResponse(BaseModel):
    """GET /integrations/{provider}/install returns the URL the
    frontend should redirect the admin to.

    We return JSON rather than a 302 so the frontend can route the
    redirect itself (avoids same-origin gotchas in the dashboard).
    """

    authorize_url: str
    provider: IntegrationProvider
