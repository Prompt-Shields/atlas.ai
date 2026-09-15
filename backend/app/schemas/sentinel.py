"""Pydantic schemas for the Microsoft Sentinel connect + forwarder router."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.sentinel_forward import SentinelDeadLetterStatus
from app.services.sentinel_schema import STREAM_NAME, TABLE_NAME
from app.services.sentinel_service import SentinelEventType, SentinelSeverity

# The four coordinates that together enable live forwarding. Supplying one
# means supplying all of them (plus the secret) — a half-filled config would
# read as "connected" while silently forwarding nothing.
_FORWARDER_FIELDS = ("azure_tenant_id", "client_id", "dce_url", "dcr_immutable_id")


class SentinelConnectRequest(BaseModel):
    """Connect-wizard payload — workspace target, mapping, and Azure config.

    The Azure Monitor fields are optional: a tenant may connect for the seeded
    preview first and supply real coordinates once their Sentinel admin has
    run the Bicep template. Live forwarding starts only when all of them (and
    the client secret) are present.
    """

    workspace_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Customer-facing Sentinel workspace label, e.g. 'Acme Corp SOC'",
    )
    table_name: str = Field(
        default=TABLE_NAME,
        min_length=1,
        max_length=255,
        description="Target Sentinel custom table",
    )
    enabled_event_types: list[SentinelEventType] = Field(
        default_factory=lambda: list(SentinelEventType),
        description="Prompt Shields event types mapped into the stream",
    )

    # ── Azure Monitor Logs Ingestion coordinates ──────────────────────
    azure_tenant_id: str | None = Field(
        default=None,
        max_length=100,
        description="The customer's Azure AD tenant id (a GUID)",
    )
    client_id: str | None = Field(
        default=None,
        max_length=100,
        description="App registration (client) id with Monitoring Metrics Publisher on the DCR",
    )
    client_secret: str | None = Field(
        default=None,
        max_length=500,
        description="App registration client secret — stored encrypted, never returned",
    )
    dce_url: str | None = Field(
        default=None,
        max_length=500,
        description="Data Collection Endpoint logs-ingestion URI",
    )
    dcr_immutable_id: str | None = Field(
        default=None,
        max_length=100,
        description="Data Collection Rule immutable id (dcr-...)",
    )
    stream_name: str = Field(
        default=STREAM_NAME,
        min_length=1,
        max_length=255,
        description="Stream declared in the DCR",
    )

    @field_validator("dce_url")
    @classmethod
    def _validate_dce_url(cls, v: str | None) -> str | None:
        if v is None:
            return None
        url = v.strip()
        if not url:
            return None
        if not url.startswith("https://"):
            # The bearer token rides this request; plaintext is never right.
            raise ValueError("dce_url must be an https:// URL")
        return url.rstrip("/")

    @field_validator("azure_tenant_id", "client_id", "dcr_immutable_id", "client_secret")
    @classmethod
    def _blank_to_none(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip() or None

    @model_validator(mode="after")
    def _forwarder_fields_all_or_nothing(self) -> SentinelConnectRequest:
        supplied = [f for f in _FORWARDER_FIELDS if getattr(self, f)]
        if not supplied:
            return self
        missing = [f for f in _FORWARDER_FIELDS if not getattr(self, f)]
        if not self.client_secret:
            missing.append("client_secret")
        if missing:
            raise ValueError(
                "live forwarding needs all of azure_tenant_id, client_id, dce_url, "
                f"dcr_immutable_id and client_secret — missing: {', '.join(missing)}"
            )
        return self


class SentinelEvent(BaseModel):
    """One row shaped like the PromptShieldsActivity_CL custom table."""

    time_generated: datetime
    event_id: str
    user: str
    ai_tool: str
    is_shadow_ai: bool
    event_type: SentinelEventType
    sensitive_type: str | None
    severity: SentinelSeverity
    detail: str
    prompt_hash: str


class SentinelEventStreamResponse(BaseModel):
    connected: bool
    workspace_name: str | None = None
    table_name: str | None = None
    enabled_event_types: list[SentinelEventType] = Field(default_factory=list)
    events: list[SentinelEvent] = Field(default_factory=list)
    # True once the Azure Monitor coordinates are stored, i.e. the seeded
    # preview above has been superseded by live forwarding.
    forwarder_configured: bool = False


# ── Forwarder ────────────────────────────────────────────────────────


class SentinelForwarderStatus(BaseModel):
    """What the admin needs to see to trust the pipe is flowing.

    Deliberately exposes the dead-letter backlog: the spec's audit guarantee
    is that nothing is silently lost, which is only meaningful if the losses
    are visible to the admin who can act on them.
    """

    connected: bool
    forwarder_configured: bool
    workspace_name: str | None = None
    table_name: str | None = None
    stream_name: str | None = None
    dcr_immutable_id: str | None = None
    enabled_event_types: list[SentinelEventType] = Field(default_factory=list)

    events_forwarded: int = 0
    events_skipped: int = 0
    batches_sent: int = 0
    batches_dead_lettered: int = 0
    pending_dead_letters: int = 0

    last_run_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None


class SentinelForwardRunResponse(BaseModel):
    """Result of an on-demand 'Forward now'."""

    events_read: int
    events_forwarded: int
    events_skipped: int
    batches_sent: int
    batches_dead_lettered: int
    error: str | None = None


class SentinelDeadLetterOut(BaseModel):
    """One dead-lettered batch, without its payload.

    The payload holds every column of every row; it is available through the
    replay CLI rather than the dashboard so the API surface stays small.
    """

    id: uuid.UUID
    status: SentinelDeadLetterStatus
    reason: str
    http_status: int | None = None
    error_detail: str | None = None
    event_count: int
    first_event_id: str | None = None
    last_event_id: str | None = None
    attempts: int
    created_at: datetime
    replayed_at: datetime | None = None


class SentinelDeadLetterListResponse(BaseModel):
    items: list[SentinelDeadLetterOut] = Field(default_factory=list)
    total: int = 0


class SentinelReplayResponse(BaseModel):
    replayed: bool
    status: SentinelDeadLetterStatus
    detail: str | None = None
