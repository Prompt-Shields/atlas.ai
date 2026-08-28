"""Sentinel forwarder — ships prompt telemetry into a customer's workspace.

Implements the MVP slice of ``docs/integrations/microsoft-sentinel/spec.md``
§7: the stream channel only. The Graph Security alerts channel, the workbook,
the analytic rules and the ASIM parser are all v1.1 and deliberately absent.

Shape of a run (``forward_pending`` → ``forward_integration``):

  1. Read the tenant's connected Sentinel integration and its forwarder config.
  2. Select prompt events after the cursor, up to a lag horizon (see
     ``COMMIT_LAG``), oldest first.
  3. Map each to a ``PromptShieldsActivity_CL`` row; pre-validate against the
     canonical schema and dead-letter anything malformed rather than poisoning
     the customer's table.
  4. POST in batches to the Logs Ingestion API, with the §6 failure handling.
  5. Advance the cursor only past events Azure Monitor confirmed accepting.

The token is a client-credentials token for the *customer's* Azure AD tenant,
cached on the integration row. The spec prefers a Federated Identity
Credential over a stored secret; FIC needs a deployed Prompt Shields app
registration to federate against, so v1 ships the client-secret path and
``docs/integrations/microsoft-sentinel/runbooks/customer-onboarding.md``
records FIC as the v1.1 upgrade.
"""

from __future__ import annotations

import asyncio
import json
import random
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.directory import DirectoryUser
from app.models.enrolled_device import EnrolledDevice
from app.models.integration import Integration, IntegrationProvider, IntegrationStatus
from app.models.prompt_event import PromptEvent
from app.models.sentinel_forward import (
    SentinelDeadLetter,
    SentinelDeadLetterStatus,
    SentinelForwardCursor,
)
from app.models.use_case import UseCase, UseCaseStatus
from app.services import sentinel_schema
from app.services.crypto import TokenEncryptionError, decrypt_token, encrypt_token
from app.services.sentinel_mapping import (
    DeviceMatch,
    DirectoryMatch,
    MappingContext,
    UnmappableEvent,
    build_row,
    normalise_tool_key,
)
from app.services.sentinel_service import SentinelEventType

logger = structlog.get_logger()

# Azure Monitor Logs Ingestion API. Pinned rather than floating: a new
# api-version can change the error envelope this module classifies on.
INGESTION_API_VERSION = "2023-01-01"
AAD_SCOPE = "https://monitor.azure.com/.default"

# Logs Ingestion accepts 1 MB / 32k items per request. We batch well under
# both: 500 events is the spec's figure, and a 900 KB budget leaves headroom
# for the JSON framing our size estimate does not model exactly.
MAX_BATCH_EVENTS = 500
MAX_BATCH_BYTES = 900_000

# `prompt_events.created_at` is a server default, so a row can be assigned a
# timestamp fractionally before a concurrent transaction commits it. Reading
# only up to `now - COMMIT_LAG` keeps such a row from landing behind an
# already-advanced cursor, which would be a silent gap in the audit trail.
COMMIT_LAG = timedelta(seconds=60)

# How far back a freshly connected integration starts. Connecting Sentinel
# should show recent activity, not replay a year of telemetry into a workspace
# the customer pays for by the gigabyte.
DEFAULT_BACKFILL = timedelta(days=1)

MAX_ATTEMPTS = 5
BASE_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 60.0
# A Retry-After far in the future means the slow lane, not a blocked worker.
MAX_HONOURED_RETRY_AFTER = 120.0

SleepFn = Callable[[float], Awaitable[None]]


# ─── Config ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ForwarderConfig:
    """The customer-supplied Azure Monitor coordinates for one tenant."""

    azure_tenant_id: str
    client_id: str
    dce_url: str
    dcr_immutable_id: str
    stream_name: str
    table_name: str
    workspace_name: str | None
    enabled_event_types: frozenset[SentinelEventType]

    @property
    def ingest_url(self) -> str:
        return (
            f"{self.dce_url.rstrip('/')}/dataCollectionRules/{self.dcr_immutable_id}"
            f"/streams/{self.stream_name}?api-version={INGESTION_API_VERSION}"
        )

    @property
    def token_url(self) -> str:
        return f"https://login.microsoftonline.com/{self.azure_tenant_id}/oauth2/v2.0/token"


# The fields that must all be present for live forwarding. Absent any of them,
# the integration stays in the v1 preview mode (seeded events, no live call).
_REQUIRED_CONFIG_KEYS = ("azure_tenant_id", "client_id", "dce_url", "dcr_immutable_id")


def load_config(integration: Integration) -> ForwarderConfig | None:
    """Parse forwarder config off the integration, or None if not configured.

    Returning None is a normal state, not an error: a tenant may connect
    Sentinel for the seeded preview and supply Azure coordinates later.
    """
    try:
        raw = json.loads(integration.config_json) if integration.config_json else {}
    except json.JSONDecodeError:
        logger.warning("sentinel_config_unparseable", integration_id=str(integration.id))
        return None
    if not isinstance(raw, dict):
        return None

    values = {key: (raw.get(key) or "").strip() for key in _REQUIRED_CONFIG_KEYS}
    if not all(values.values()):
        return None

    enabled = raw.get("enabled_event_types") or []
    types: set[SentinelEventType] = set()
    for value in enabled:
        try:
            types.add(SentinelEventType(value))
        except ValueError:
            logger.warning("sentinel_unknown_event_type", value=value)

    return ForwarderConfig(
        azure_tenant_id=values["azure_tenant_id"],
        client_id=values["client_id"],
        dce_url=values["dce_url"],
        dcr_immutable_id=values["dcr_immutable_id"],
        stream_name=(raw.get("stream_name") or sentinel_schema.STREAM_NAME).strip(),
        table_name=(raw.get("table_name") or sentinel_schema.TABLE_NAME).strip(),
        workspace_name=(raw.get("workspace_name") or None),
        enabled_event_types=frozenset(types or set(SentinelEventType)),
    )


# ─── Token acquisition ───────────────────────────────────────────────


class SentinelAuthError(Exception):
    """Azure AD refused the client-credentials exchange."""


async def fetch_access_token(
    client: httpx.AsyncClient, config: ForwarderConfig, client_secret: str
) -> tuple[str, datetime]:
    """Client-credentials token for the customer tenant, with its expiry."""
    resp = await client.post(
        config.token_url,
        data={
            "grant_type": "client_credentials",
            "client_id": config.client_id,
            "client_secret": client_secret,
            "scope": AAD_SCOPE,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if resp.status_code != 200:
        raise SentinelAuthError(f"token endpoint returned {resp.status_code}: {resp.text[:300]}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise SentinelAuthError("token endpoint returned non-JSON") from exc

    token = body.get("access_token")
    if not token:
        raise SentinelAuthError("token response carried no access_token")
    # Expire a minute early so a token never dies mid-batch.
    expires_in = int(body.get("expires_in") or 3600)
    expires_at = datetime.now(UTC) + timedelta(seconds=max(60, expires_in - 60))
    return token, expires_at


class TokenProvider:
    """Caches the customer-tenant token on the integration row.

    ``Integration.refresh_token_encrypted`` holds the long-lived client secret
    and ``access_token_encrypted`` the short-lived bearer token — the same
    split the OAuth integrations use, so key rotation and the Fernet layer
    behave identically here.
    """

    def __init__(
        self,
        db: AsyncSession,
        client: httpx.AsyncClient,
        integration: Integration,
        config: ForwarderConfig,
    ) -> None:
        self._db = db
        self._client = client
        self._integration = integration
        self._config = config

    def _client_secret(self) -> str:
        ciphertext = self._integration.refresh_token_encrypted
        if not ciphertext:
            raise SentinelAuthError("integration has no stored client secret — reconnect Sentinel")
        try:
            return decrypt_token(ciphertext)
        except TokenEncryptionError as exc:
            raise SentinelAuthError(f"client secret does not decrypt: {exc}") from exc

    async def get(self, *, force_refresh: bool = False) -> str:
        integration = self._integration
        if not force_refresh and integration.access_token_encrypted:
            expires_at = integration.access_token_expires_at
            if expires_at is not None:
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=UTC)
                if expires_at > datetime.now(UTC):
                    try:
                        return decrypt_token(integration.access_token_encrypted)
                    except TokenEncryptionError:
                        # Unreadable cache is not fatal — fetch a fresh token.
                        logger.warning(
                            "sentinel_cached_token_undecryptable",
                            integration_id=str(integration.id),
                        )

        token, expires_at = await fetch_access_token(
            self._client, self._config, self._client_secret()
        )
        integration.access_token_encrypted = encrypt_token(token)
        integration.access_token_expires_at = expires_at
        await self._db.flush()
        return token


# ─── Batching ────────────────────────────────────────────────────────


def batch_rows(
    rows: Sequence[dict[str, Any]],
    *,
    max_events: int = MAX_BATCH_EVENTS,
    max_bytes: int = MAX_BATCH_BYTES,
) -> list[list[dict[str, Any]]]:
    """Split wire rows into requests under the item and byte caps.

    A single row larger than ``max_bytes`` still gets its own batch: the send
    path turns the resulting 413 into a dead letter with a clear reason, which
    beats dropping it here without a record.
    """
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_bytes = 2  # the enclosing "[]"

    for row in rows:
        size = len(json.dumps(row, default=str).encode("utf-8")) + 1  # +1 for ","
        if current and (len(current) >= max_events or current_bytes + size > max_bytes):
            batches.append(current)
            current = []
            current_bytes = 2
        current.append(row)
        current_bytes += size

    if current:
        batches.append(current)
    return batches


# ─── Send ────────────────────────────────────────────────────────────


@dataclass
class DeadLetterRecord:
    """A batch that could not be delivered, ready to persist."""

    reason: str
    http_status: int | None
    detail: str
    rows: list[dict[str, Any]]
    attempts: int


@dataclass
class SendResult:
    accepted: list[dict[str, Any]]
    dead_letters: list[DeadLetterRecord]


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff with full jitter, capped.

    Full jitter (uniform over [0, ceiling]) rather than a fixed delay so that
    many tenants retrying after the same Azure outage spread out instead of
    stampeding it again in lockstep.
    """
    ceiling = min(MAX_BACKOFF_SECONDS, BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)))
    # noqa S311: retry jitter is a scheduling concern, not a security one.
    return random.uniform(0, ceiling)  # noqa: S311


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None  # HTTP-date form; fall back to ordinary backoff.
    return max(0.0, min(seconds, MAX_HONOURED_RETRY_AFTER))


async def send_batch(
    client: httpx.AsyncClient,
    config: ForwarderConfig,
    rows: list[dict[str, Any]],
    *,
    tokens: TokenProvider,
    sleep: SleepFn | None = None,
) -> SendResult:
    """POST one batch, applying the spec §6 failure table.

    Returns which rows Azure Monitor confirmed accepting and which became dead
    letters. Never raises for a delivery failure — an unsendable batch is data
    to record, not an exception to unwind the whole run.
    """
    sleeper: SleepFn = sleep if sleep is not None else asyncio.sleep
    if not rows:
        return SendResult(accepted=[], dead_letters=[])

    refreshed_once = False
    last_detail = "no attempt made"
    last_status: int | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            token = await tokens.get(force_refresh=refreshed_once and attempt > 1)
        except SentinelAuthError as exc:
            return SendResult(
                accepted=[],
                dead_letters=[
                    DeadLetterRecord(
                        reason="auth_failed",
                        http_status=None,
                        detail=str(exc)[:2000],
                        rows=rows,
                        attempts=attempt,
                    )
                ],
            )

        try:
            resp = await client.post(
                config.ingest_url,
                content=json.dumps(rows, default=str).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            # Network blip: retry with jittered exponential backoff.
            last_detail = f"{type(exc).__name__}: {exc}"
            last_status = None
            if attempt < MAX_ATTEMPTS:
                await sleeper(_backoff_seconds(attempt))
                continue
            break

        status = resp.status_code
        last_status = status
        last_detail = (resp.text or "")[:2000]

        if 200 <= status < 300:
            return SendResult(accepted=rows, dead_letters=[])

        if status == 401:
            # Token expired mid-flight — refresh and retry once, then treat a
            # second 401 as a credential problem rather than a transient one.
            if not refreshed_once:
                refreshed_once = True
                await tokens.get(force_refresh=True)
                continue
            return SendResult(
                accepted=[],
                dead_letters=[
                    DeadLetterRecord(
                        reason="http_401",
                        http_status=401,
                        detail=last_detail,
                        rows=rows,
                        attempts=attempt,
                    )
                ],
            )

        if status == 403:
            # DCR permissions. The customer's SOC cannot fix this; it is an
            # operational alert for the Prompt Shields admin (spec §6).
            logger.error(
                "sentinel_forward_forbidden",
                dcr=config.dcr_immutable_id,
                detail=last_detail[:200],
            )
            return SendResult(
                accepted=[],
                dead_letters=[
                    DeadLetterRecord(
                        reason="http_403",
                        http_status=403,
                        detail=last_detail,
                        rows=rows,
                        attempts=attempt,
                    )
                ],
            )

        if status == 413:
            if len(rows) == 1:
                return SendResult(
                    accepted=[],
                    dead_letters=[
                        DeadLetterRecord(
                            reason="payload_too_large",
                            http_status=413,
                            detail=last_detail,
                            rows=rows,
                            attempts=attempt,
                        )
                    ],
                )
            # Halve and send each half independently, so one oversized row
            # cannot strand the rest of the batch.
            midpoint = len(rows) // 2
            first = await send_batch(client, config, rows[:midpoint], tokens=tokens, sleep=sleep)
            second = await send_batch(client, config, rows[midpoint:], tokens=tokens, sleep=sleep)
            return SendResult(
                accepted=first.accepted + second.accepted,
                dead_letters=first.dead_letters + second.dead_letters,
            )

        if status == 429:
            delay = _parse_retry_after(resp.headers.get("Retry-After"))
            if attempt < MAX_ATTEMPTS:
                await sleeper(delay if delay is not None else _backoff_seconds(attempt))
                continue
            break

        if status >= 500:
            if attempt < MAX_ATTEMPTS:
                await sleeper(_backoff_seconds(attempt))
                continue
            break

        # Any other 4xx is a request we should not repeat unchanged.
        return SendResult(
            accepted=[],
            dead_letters=[
                DeadLetterRecord(
                    reason=f"http_{status}",
                    http_status=status,
                    detail=last_detail,
                    rows=rows,
                    attempts=attempt,
                )
            ],
        )

    return SendResult(
        accepted=[],
        dead_letters=[
            DeadLetterRecord(
                reason="exhausted_retries",
                http_status=last_status,
                detail=last_detail,
                rows=rows,
                attempts=MAX_ATTEMPTS,
            )
        ],
    )


# ─── Lookups ─────────────────────────────────────────────────────────


async def build_mapping_context(
    db: AsyncSession, tenant_id: uuid.UUID, events: Sequence[PromptEvent]
) -> MappingContext:
    """Pre-fetch the directory / device / sanctioned-tool data one run needs.

    Three bounded queries per run instead of three per event. Every query
    carries an explicit ``tenant_id`` filter as well as running under RLS —
    the repo's tenancy rule is that we never rely on RLS alone.
    """
    emails = {
        e.user_external_id.strip().lower()
        for e in events
        if e.user_external_id and e.user_external_id.strip()
    }
    fingerprints = {
        e.device_fingerprint.strip()
        for e in events
        if e.device_fingerprint and e.device_fingerprint.strip()
    }

    directory: dict[str, DirectoryMatch] = {}
    if emails:
        rows = (
            await db.execute(
                select(DirectoryUser).where(
                    DirectoryUser.tenant_id == tenant_id,
                    DirectoryUser.email.in_(emails),
                )
            )
        ).scalars()
        for row in rows:
            if row.email:
                directory[row.email.lower()] = DirectoryMatch(
                    aad_object_id=row.external_user_id or None,
                    department=row.department or None,
                )

    devices: dict[str, DeviceMatch] = {}
    if fingerprints:
        rows = (
            await db.execute(
                select(EnrolledDevice).where(
                    EnrolledDevice.tenant_id == tenant_id,
                    EnrolledDevice.fingerprint.in_(fingerprints),
                )
            )
        ).scalars()
        for row in rows:
            if row.fingerprint:
                # NOTE: this is our own EnrolledDevice id, not the Intune
                # device id data-schema.md envisages for a KQL join against
                # `IntuneDevices`. `prompt_events` identifies an endpoint only
                # by agent fingerprint, and correlating that to an MDM record
                # is the managed-device unification work
                # (docs/design/managed-device-unification.md). Until that
                # lands, `EndpointId` groups a customer's own events
                # consistently but does not join Microsoft's tables — see the
                # caveat in kql-samples.md.
                devices[row.fingerprint] = DeviceMatch(
                    endpoint_id=str(row.id),
                    platform=row.platform or None,
                )

    tools = (
        await db.execute(
            select(UseCase.tool).where(
                UseCase.tenant_id == tenant_id,
                UseCase.status == UseCaseStatus.ACTIVE,
            )
        )
    ).scalars()
    sanctioned = {normalise_tool_key(t) for t in tools}
    sanctioned.discard("")

    return MappingContext(
        directory_by_user=directory,
        devices_by_fingerprint=devices,
        sanctioned_tools=frozenset(sanctioned),
    )


async def _load_cursor(db: AsyncSession, integration: Integration) -> SentinelForwardCursor:
    cursor = (
        await db.execute(
            select(SentinelForwardCursor).where(
                SentinelForwardCursor.tenant_id == integration.tenant_id,
                SentinelForwardCursor.integration_id == integration.id,
            )
        )
    ).scalar_one_or_none()
    if cursor is None:
        cursor = SentinelForwardCursor(
            tenant_id=integration.tenant_id,
            integration_id=integration.id,
        )
        db.add(cursor)
        await db.flush()
    return cursor


async def _select_events(
    db: AsyncSession,
    integration: Integration,
    cursor: SentinelForwardCursor,
    *,
    limit: int,
    backfill: timedelta,
) -> list[PromptEvent]:
    """Events after the cursor, oldest first, up to the lag horizon."""
    horizon = datetime.now(UTC) - COMMIT_LAG
    query = select(PromptEvent).where(
        PromptEvent.tenant_id == integration.tenant_id,
        PromptEvent.created_at <= horizon,
    )

    if cursor.last_event_created_at is None:
        query = query.where(PromptEvent.created_at >= datetime.now(UTC) - backfill)
    else:
        last_at = cursor.last_event_created_at
        if last_at.tzinfo is None:
            last_at = last_at.replace(tzinfo=UTC)
        if cursor.last_event_id is None:
            # Defensive: the two cursor columns are always written together, so
            # this should be unreachable. Fall back to the timestamp alone
            # rather than comparing against NULL, which SQL evaluates to NULL
            # and would silently drop every same-timestamp row.
            query = query.where(PromptEvent.created_at > last_at)
        else:
            # Strict `(created_at, id)` ordering: same-timestamp rows are
            # disambiguated by id so none is skipped and none is re-sent.
            query = query.where(
                or_(
                    PromptEvent.created_at > last_at,
                    and_(
                        PromptEvent.created_at == last_at,
                        PromptEvent.id > cursor.last_event_id,
                    ),
                )
            )

    query = query.order_by(PromptEvent.created_at, PromptEvent.id).limit(limit)
    return list((await db.execute(query)).scalars())


def _record_dead_letter(
    db: AsyncSession,
    integration: Integration,
    record: DeadLetterRecord,
) -> None:
    event_ids = [str(r.get("EventId")) for r in record.rows if r.get("EventId")]
    db.add(
        SentinelDeadLetter(
            tenant_id=integration.tenant_id,
            integration_id=integration.id,
            status=SentinelDeadLetterStatus.PENDING,
            reason=record.reason[:100],
            http_status=record.http_status,
            error_detail=record.detail[:2000] if record.detail else None,
            payload=record.rows,
            event_count=len(record.rows),
            first_event_id=event_ids[0][:100] if event_ids else None,
            last_event_id=event_ids[-1][:100] if event_ids else None,
            attempts=record.attempts,
        )
    )


# ─── Orchestration ───────────────────────────────────────────────────


@dataclass
class ForwardResult:
    """Outcome of one ``forward_integration`` call."""

    events_read: int = 0
    events_forwarded: int = 0
    events_skipped: int = 0
    batches_sent: int = 0
    batches_dead_lettered: int = 0
    error: str | None = None


async def forward_integration(
    db: AsyncSession,
    integration: Integration,
    *,
    client: httpx.AsyncClient,
    limit: int = 1000,
    backfill: timedelta = DEFAULT_BACKFILL,
    sleep: SleepFn | None = None,
) -> ForwardResult:
    """Forward one integration's pending events. Never raises.

    Fault-isolating in the same way as ``cost.sync_service.sync_integration``:
    a failure is recorded on the integration and returned, so one broken
    customer config cannot abort the sweep for everyone else.
    """
    result = ForwardResult()
    cursor = await _load_cursor(db, integration)
    cursor.last_run_at = datetime.now(UTC)

    config = load_config(integration)
    if config is None:
        # Preview-only connect: nothing to forward, and not an error state.
        result.error = None
        await db.commit()
        return result

    try:
        events = await _select_events(db, integration, cursor, limit=limit, backfill=backfill)
        result.events_read = len(events)
        if not events:
            integration.last_synced_at = datetime.now(UTC)
            cursor.last_success_at = datetime.now(UTC)
            cursor.last_error = None
            await db.commit()
            return result

        context = await build_mapping_context(db, integration.tenant_id, events)

        # Map, filtering to the tenant's enabled types. `row_events` keeps each
        # wire row paired with its source event so the cursor can advance to
        # exactly the last accepted one.
        row_events: list[tuple[PromptEvent, dict[str, Any]]] = []
        invalid: list[DeadLetterRecord] = []

        for event in events:
            try:
                row = build_row(event, tenant_id=integration.tenant_id, context=context)
            except UnmappableEvent:
                # Ordinary activity or a row we cannot represent. Not a
                # delivery failure — the cursor still moves past it.
                result.events_skipped += 1
                continue

            if SentinelEventType(row["EventType"]) not in config.enabled_event_types:
                result.events_skipped += 1
                continue

            wire = sentinel_schema.serialise_row(row)
            reasons = sentinel_schema.validate_row(row)
            if reasons:
                # Reject at source (spec §6 schema drift) — visible in the
                # dead-letter queue, never sent.
                invalid.append(
                    DeadLetterRecord(
                        reason="schema_invalid",
                        http_status=None,
                        detail="; ".join(reasons),
                        rows=[wire],
                        attempts=0,
                    )
                )
                result.events_skipped += 1
                continue

            row_events.append((event, wire))

        for record in invalid:
            _record_dead_letter(db, integration, record)
            result.batches_dead_lettered += 1

        tokens = TokenProvider(db, client, integration, config)
        rows = [wire for _, wire in row_events]

        for batch in batch_rows(rows):
            send = await send_batch(client, config, batch, tokens=tokens, sleep=sleep)

            if send.accepted:
                result.batches_sent += 1
                result.events_forwarded += len(send.accepted)

            for record in send.dead_letters:
                _record_dead_letter(db, integration, record)
                result.batches_dead_lettered += 1

        # Every event in this window now has a durable outcome: accepted by
        # Azure Monitor, skipped for a recorded reason, or sitting in the
        # dead-letter queue with its full payload. That is exactly the spec's
        # audit guarantee — "lands in Sentinel or is visibly in the
        # dead-letter queue" — so the cursor advances past the whole window.
        #
        # It advances here and nowhere else, in the same transaction as the
        # dead-letter inserts below, so the two commit together: a crash
        # before the commit re-reads the window rather than losing it.
        #
        # Holding the cursor back on a failure instead would re-read the same
        # events every cycle and write a fresh duplicate dead letter each
        # time — a 403 lasting a day would bury the operator's queue in
        # thousands of copies of one batch. Replay is what re-delivers a dead
        # letter; the cursor is not.
        if events:
            cursor.last_event_created_at = events[-1].created_at
            cursor.last_event_id = events[-1].id

        cursor.events_forwarded += result.events_forwarded
        cursor.events_skipped += result.events_skipped
        cursor.batches_sent += result.batches_sent
        cursor.batches_dead_lettered += result.batches_dead_lettered

        if result.batches_dead_lettered:
            cursor.last_error = f"{result.batches_dead_lettered} batch(es) dead-lettered"
            integration.status = IntegrationStatus.ERROR
            integration.last_error = cursor.last_error[:500]
        else:
            cursor.last_error = None
            cursor.last_success_at = datetime.now(UTC)
            integration.status = IntegrationStatus.CONNECTED
            integration.last_error = None
            integration.last_synced_at = datetime.now(UTC)

        await db.commit()
        return result

    except Exception as exc:  # noqa: BLE001 — fault isolation, see docstring
        await db.rollback()
        message = f"{type(exc).__name__}: {exc}"
        logger.error(
            "sentinel_forward_failed",
            integration_id=str(integration.id),
            tenant_id=str(integration.tenant_id),
            error=message,
        )
        result.error = message
        # Re-load state the rollback discarded, then record the failure.
        cursor = await _load_cursor(db, integration)
        cursor.last_run_at = datetime.now(UTC)
        cursor.last_error = message[:500]
        integration.status = IntegrationStatus.ERROR
        integration.last_error = message[:500]
        await db.commit()
        return result


async def forward_pending(
    db: AsyncSession,
    *,
    client: httpx.AsyncClient,
    batch_size: int = 1000,
    sleep: SleepFn | None = None,
) -> int:
    """Forward every connected Sentinel integration visible to this session.

    Returns the number of events forwarded. The caller supplies the tenant
    scoping — the worker loops tenants under ``tenant_scoped_session`` so RLS
    applies to both the reads and the cursor writes.
    """
    integrations = list(
        (
            await db.execute(
                select(Integration).where(
                    Integration.provider == IntegrationProvider.SENTINEL,
                    Integration.is_active.is_(True),
                    Integration.status.in_([IntegrationStatus.CONNECTED, IntegrationStatus.ERROR]),
                )
            )
        ).scalars()
    )

    forwarded = 0
    for integration in integrations:
        result = await forward_integration(
            db, integration, client=client, limit=batch_size, sleep=sleep
        )
        forwarded += result.events_forwarded
    return forwarded


# ─── Replay ──────────────────────────────────────────────────────────


async def replay_dead_letter(
    db: AsyncSession,
    dead_letter: SentinelDeadLetter,
    integration: Integration,
    *,
    client: httpx.AsyncClient,
    sleep: SleepFn | None = None,
) -> bool:
    """Re-send one dead-lettered batch. True when Azure Monitor accepted it.

    The stored payload goes back out unchanged, so every row carries the
    ``EventId`` it had originally — a batch that was partly ingested before the
    failure re-ingests as the same identifiers rather than as new events.
    """
    config = load_config(integration)
    if config is None:
        raise ValueError("integration has no forwarder config to replay against")

    rows = list(dead_letter.payload or [])
    if not rows:
        dead_letter.status = SentinelDeadLetterStatus.DISCARDED
        dead_letter.error_detail = "empty payload — nothing to replay"
        await db.commit()
        return False

    tokens = TokenProvider(db, client, integration, config)
    send = await send_batch(client, config, rows, tokens=tokens, sleep=sleep)

    dead_letter.attempts += 1
    if send.dead_letters:
        failure = send.dead_letters[0]
        dead_letter.reason = failure.reason[:100]
        dead_letter.http_status = failure.http_status
        dead_letter.error_detail = (failure.detail or "")[:2000] or None
        await db.commit()
        return False

    dead_letter.status = SentinelDeadLetterStatus.REPLAYED
    dead_letter.replayed_at = datetime.now(UTC)
    dead_letter.error_detail = None
    await db.commit()
    return True
