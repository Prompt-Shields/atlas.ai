"""Cross-MDM endpoint-compliance aggregate router.

Backs `/dashboard/endpoints` with live data from `ManagedDevice`
joined to `Integration.provider`. Two surfaces:

  GET /api/v1/endpoints/compliance
    Returns per-MDM aggregates suitable for the dashboard's MDM
    cards: enrolled count, compliant count, extension-installed
    count (heuristic until the agent register-heartbeat lands),
    last sync timestamp.

  GET /api/v1/endpoints/devices?provider=<...>
    Returns the device detail rows for a specific MDM.

Tenant-scoped throughout. Cross-MDM admins see the whole tenant
roster; SuperAdmin sees their own tenant only (super-admin
cross-tenant fetches go via /admin endpoints).

Extension-install signal
  Real data, joined against ExtensionDeviceHeartbeat (PR #64). A
  device counts as "extension installed" iff its enrolled user
  has at least one heartbeat within EXTENSION_FRESH_WINDOW
  (default 7 days). Devices with no enrolled user email are not
  counted — the IT lead's first action there is to assign one in
  the MDM anyway. The deprecated compliance + 3-day-fresh
  heuristic was removed in v0.5 (see _is_extension_installed).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthUser
from app.database import get_db_session
from app.models.extension_heartbeat import ExtensionDeviceHeartbeat
from app.models.integration import (
    Integration,
    IntegrationProvider,
    IntegrationStatus,
)
from app.models.managed_device import ManagedDevice
from app.routers.extension import EXTENSION_FRESH_WINDOW

router = APIRouter(prefix="/endpoints", tags=["Endpoints"])


# ─── Schemas ─────────────────────────────────────────────────────────


class EndpointMdmAggregate(BaseModel):
    mdm_provider: str
    display_name: str
    vendor: Literal["microsoft", "other"]
    enrolled_count: int
    compliant_count: int
    extension_installed_count: int
    last_synced_at: datetime | None


class EndpointComplianceResponse(BaseModel):
    aggregates: list[EndpointMdmAggregate]
    total_enrolled: int
    total_compliant: int
    total_extension_installed: int


class EndpointDeviceResponse(BaseModel):
    id: str
    mdm_provider: str
    device_name: str
    operating_system: str | None
    os_version: str | None
    enrolled_user_email: str | None
    compliance_state: str | None
    extension_installed: bool
    last_seen_at: datetime | None


class EndpointDevicesResponse(BaseModel):
    devices: list[EndpointDeviceResponse]
    total: int


# ─── Constants ───────────────────────────────────────────────────────


# Maps the IntegrationProvider enum to display + vendor metadata.
# Keep in sync with services.integration_registry — duplicated here
# rather than imported to keep the router cheap.
_MDM_META: dict[IntegrationProvider, tuple[str, str]] = {
    IntegrationProvider.MICROSOFT_INTUNE: ("Microsoft Intune", "microsoft"),
    IntegrationProvider.JAMF_PRO: ("Jamf Pro", "other"),
    IntegrationProvider.KANDJI: ("Kandji", "other"),
    IntegrationProvider.JUMPCLOUD: ("JumpCloud", "other"),
}


async def _fresh_heartbeat_emails(db: AsyncSession, tenant_id, now: datetime) -> set[str]:
    """Return the set of lowercased user_email values that have a
    fresh ExtensionDeviceHeartbeat for this tenant (within
    EXTENSION_FRESH_WINDOW, default 7 days).

    Used to compute extension_installed counts via a join against
    ManagedDevice.enrolled_user_email. Heartbeats with NULL
    user_email are ignored — we can't attribute them to a managed
    device without a user identifier.
    """
    cutoff = now - EXTENSION_FRESH_WINDOW
    rows = (
        (
            await db.execute(
                select(ExtensionDeviceHeartbeat.user_email)
                .where(
                    ExtensionDeviceHeartbeat.tenant_id == tenant_id,
                    ExtensionDeviceHeartbeat.last_seen_at >= cutoff,
                    ExtensionDeviceHeartbeat.user_email.is_not(None),
                )
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    return {(email or "").lower() for email in rows if email}


def _is_extension_installed(
    enrolled_user_email: str | None,
    fresh_emails: set[str],
) -> bool:
    """Real-data successor to the deprecated 3-day-fresh heuristic.

    A device counts as "extension installed" iff its enrolled user
    has at least one ExtensionDeviceHeartbeat within the freshness
    window. NULL email → not counted (we can't attribute the device
    to a user); the IT lead's first action on a NULL-owner device is
    to assign one in the MDM anyway.
    """
    if not enrolled_user_email:
        return False
    return enrolled_user_email.lower() in fresh_emails


# ─── Endpoints ───────────────────────────────────────────────────────


@router.get("/compliance", response_model=EndpointComplianceResponse)
async def list_compliance(
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> EndpointComplianceResponse:
    """Per-MDM aggregates for the caller's tenant.

    Renders one aggregate per supported MDM provider — even those the
    tenant hasn't connected (returned with all zeros + null
    last_synced_at). The frontend uses the zero rows to render the
    "Not connected" state alongside live data.
    """
    # ── Per-provider integration metadata (last sync time) ───────
    int_q = select(Integration).where(
        Integration.tenant_id == user.tenant_id,
        Integration.provider.in_(list(_MDM_META.keys())),
        Integration.status == IntegrationStatus.CONNECTED,
    )
    integrations = list((await db.execute(int_q)).scalars().all())
    last_sync_by_provider: dict[IntegrationProvider, datetime | None] = {}
    for ig in integrations:
        # Pick the most-recent sync per provider (admin could have
        # multiple Integration rows in theory; today there's at most
        # one per (tenant, provider) per the unique constraint).
        existing = last_sync_by_provider.get(ig.provider)
        if existing is None or (ig.last_synced_at is not None and existing < ig.last_synced_at):
            last_sync_by_provider[ig.provider] = ig.last_synced_at

    # ── Devices grouped by provider (via integration_id join) ────
    # Pull enrolled_user_email so we can attribute the heartbeat join
    # per row. Last_synced_at is still selected even though we no
    # longer use it for the extension-installed flag — kept for
    # future per-row freshness UI.
    device_rows_q = (
        select(
            Integration.provider,
            ManagedDevice.compliance_state,
            ManagedDevice.last_synced_at,
            ManagedDevice.enrolled_user_email,
        )
        .join(Integration, ManagedDevice.integration_id == Integration.id)
        .where(ManagedDevice.tenant_id == user.tenant_id)
    )
    rows = (await db.execute(device_rows_q)).all()

    # ── Fresh heartbeat emails — one query, then in-memory join ──
    fresh_emails = await _fresh_heartbeat_emails(db, user.tenant_id, datetime.now(UTC))

    # ── Roll up counts per provider ──────────────────────────────
    counts: dict[IntegrationProvider, dict[str, int]] = {}
    for provider in _MDM_META:
        counts[provider] = {
            "enrolled": 0,
            "compliant": 0,
            "extension": 0,
        }
    for provider, compliance_state, _last_synced_at, enrolled_user_email in rows:
        if provider not in counts:
            continue
        counts[provider]["enrolled"] += 1
        if compliance_state == "compliant":
            counts[provider]["compliant"] += 1
        if _is_extension_installed(enrolled_user_email, fresh_emails):
            counts[provider]["extension"] += 1

    aggregates: list[EndpointMdmAggregate] = []
    total_enrolled = 0
    total_compliant = 0
    total_extension = 0
    for provider, (display_name, vendor) in _MDM_META.items():
        c = counts[provider]
        aggregates.append(
            EndpointMdmAggregate(
                mdm_provider=provider.value,
                display_name=display_name,
                vendor=vendor,  # type: ignore[arg-type]
                enrolled_count=c["enrolled"],
                compliant_count=c["compliant"],
                extension_installed_count=c["extension"],
                last_synced_at=last_sync_by_provider.get(provider),
            )
        )
        total_enrolled += c["enrolled"]
        total_compliant += c["compliant"]
        total_extension += c["extension"]

    return EndpointComplianceResponse(
        aggregates=aggregates,
        total_enrolled=total_enrolled,
        total_compliant=total_compliant,
        total_extension_installed=total_extension,
    )


@router.get("/devices", response_model=EndpointDevicesResponse)
async def list_devices(
    user: AuthUser,
    provider: IntegrationProvider = Query(
        ...,
        description="MDM provider whose devices to return",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db_session),
) -> EndpointDevicesResponse:
    """Per-MDM device detail list for the dashboard's drill-down table."""

    count_q = (
        select(func.count(ManagedDevice.id))
        .join(Integration, ManagedDevice.integration_id == Integration.id)
        .where(
            ManagedDevice.tenant_id == user.tenant_id,
            Integration.provider == provider,
        )
    )
    total = int((await db.execute(count_q)).scalar() or 0)

    device_q = (
        select(ManagedDevice, Integration.provider)
        .join(Integration, ManagedDevice.integration_id == Integration.id)
        .where(
            ManagedDevice.tenant_id == user.tenant_id,
            Integration.provider == provider,
        )
        .order_by(ManagedDevice.last_synced_at.desc().nulls_last())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    pairs = (await db.execute(device_q)).all()

    # Fresh heartbeat emails — one query for the whole page rather
    # than per-device lookups.
    fresh_emails = await _fresh_heartbeat_emails(db, user.tenant_id, datetime.now(UTC))

    devices: list[EndpointDeviceResponse] = []
    for device, prov in pairs:
        devices.append(
            EndpointDeviceResponse(
                id=str(device.id),
                mdm_provider=prov.value,
                device_name=device.device_name,
                operating_system=device.operating_system,
                os_version=device.os_version,
                enrolled_user_email=device.enrolled_user_email,
                compliance_state=device.compliance_state,
                extension_installed=_is_extension_installed(
                    device.enrolled_user_email, fresh_emails
                ),
                last_seen_at=device.last_sync_to_mdm_at or device.last_synced_at,
            )
        )
    return EndpointDevicesResponse(devices=devices, total=total)
