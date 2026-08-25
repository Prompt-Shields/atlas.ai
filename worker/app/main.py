"""Worker entry point — dispatches based on WORKER_MODE env var.

Modes:
- dispatcher: Polls outbox and delivers events via SSE (runs continuously)
- risk_analyzer: Processes pending blobs through risk analysis (runs once, exits)
- correlation_engine: Correlates risks and creates action plans (runs once, exits)
- slack_dispatcher: Picks up PENDING SurveyDelivery rows and sends DMs (runs continuously)

Azure Container Apps Jobs:
- risk_analyzer and correlation_engine run as scheduled jobs (cron triggers)
- dispatcher + slack_dispatcher run as continuous container apps
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import uuid

import structlog

# Add parent to path so we can import the backend app module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import get_settings
from app.database import close_db, get_standalone_session, init_db
from app.observability import setup_logging

logger = structlog.get_logger()

_shutdown = False


def handle_shutdown(signum: int, frame: object) -> None:
    global _shutdown
    _shutdown = True
    logger.info("shutdown_signal_received", signal=signum)


async def run_dispatcher() -> None:
    """Continuously poll outbox and deliver events."""
    from app.services.dispatch_service import process_outbox

    settings = get_settings()
    interval = settings.worker_poll_interval_seconds

    logger.info("dispatcher_started", poll_interval=interval)

    while not _shutdown:
        try:
            async with get_standalone_session() as db:
                processed = await process_outbox(db, batch_size=50)
                if processed > 0:
                    logger.info("dispatcher_batch", processed=processed)
        except Exception as exc:
            logger.error("dispatcher_error", error=str(exc))

        await asyncio.sleep(interval)

    logger.info("dispatcher_stopped")


async def run_risk_analyzer() -> None:
    """Process pending blobs through risk analysis. Runs once and exits."""
    from app.agents.risk_analyzer import process_batch

    settings = get_settings()
    job_id = f"risk-{uuid.uuid4()}"

    logger.info("risk_analyzer_job_started", job_id=job_id)

    try:
        async with get_standalone_session() as db:
            risks = await process_batch(
                db,
                job_id=job_id,
                limit=settings.worker_batch_size,
            )
            logger.info("risk_analyzer_job_complete", job_id=job_id, risks_created=len(risks))
    except Exception as exc:
        logger.error("risk_analyzer_job_failed", job_id=job_id, error=str(exc))
        sys.exit(1)


async def run_correlation_engine() -> None:
    """Correlate risks and create action plans. Runs once per tenant with pending risks."""
    from app.agents.correlation_engine import correlate_risks
    from sqlalchemy import select, distinct
    from app.models.risk import RiskMitigation

    job_id = f"corr-{uuid.uuid4()}"
    logger.info("correlation_engine_job_started", job_id=job_id)

    try:
        async with get_standalone_session() as db:
            # Find tenants/orgs with recently created risks
            result = await db.execute(
                select(
                    distinct(RiskMitigation.tenant_id),
                    RiskMitigation.org_id,
                )
                .where(RiskMitigation.processing_status == "completed")
                .order_by(RiskMitigation.tenant_id)
            )
            tenant_orgs = list(result.all())

            total_correlations = 0
            for tenant_id, org_id in tenant_orgs:
                correlations = await correlate_risks(
                    db,
                    tenant_id=tenant_id,
                    org_id=org_id,
                    job_id=job_id,
                )
                total_correlations += len(correlations)

            logger.info(
                "correlation_engine_job_complete",
                job_id=job_id,
                tenant_org_pairs=len(tenant_orgs),
                correlations_created=total_correlations,
            )
    except Exception as exc:
        logger.error("correlation_engine_job_failed", job_id=job_id, error=str(exc))
        sys.exit(1)


async def run_device_risk_engine() -> None:
    """Cron job: scan each tenant for at-risk users, emit risk nudges. Run-once."""
    from sqlalchemy import select

    from app.models.tenant import Tenant
    from app.services.device_risk_engine import sweep_tenants
    from worker_app.tenant_session import tenant_scoped_session

    job_id = f"device-risk-{uuid.uuid4()}"
    logger.info("device_risk_engine_job_started", job_id=job_id)

    def _log_tenant_failure(tenant_id: uuid.UUID, exc: Exception) -> None:
        logger.warning(
            "device_risk_engine_tenant_failed",
            job_id=job_id,
            tenant_id=str(tenant_id),
            error=str(exc),
        )

    try:
        # Enumerate tenants with an unrestricted (admin) session.
        async with get_standalone_session() as admin_db:
            tenant_ids = (await admin_db.execute(select(Tenant.id))).scalars().all()
    except Exception as exc:
        # Enumeration is the one genuinely fatal step — without the tenant list
        # there is no sweep to run.
        logger.error("device_risk_engine_job_failed", job_id=job_id, error=str(exc))
        sys.exit(1)

    # One tenant's failure never aborts the sweep; see sweep_tenants().
    result = await sweep_tenants(
        tenant_ids, tenant_scoped_session, on_error=_log_tenant_failure
    )

    logger.info(
        "device_risk_engine_job_complete",
        job_id=job_id,
        tenants=len(tenant_ids),
        tenants_swept=result.tenants_swept,
        tenants_failed=len(result.failed_tenant_ids),
        emitted=result.emitted,
    )

    # Surface a partial sweep to the Container Apps job runner, but only after
    # every healthy tenant has been processed. The job's replicaRetryLimit of 2
    # then gives the failed tenants another attempt; the 24h per-device cooldown
    # makes the retry a no-op for anyone already nudged.
    if result.failed_tenant_ids:
        sys.exit(1)


async def run_slack_dispatcher() -> None:
    """Continuously poll PENDING SurveyDelivery rows and send DMs.

    See `app.services.survey_dispatch_slack.dispatch_pending`. Each
    iteration picks a batch, dispatches all DMs in it (rate-limited
    at 1/sec per workspace), then sleeps `WORKER_POLL_INTERVAL_SECONDS`.
    """
    import httpx
    from app.services.survey_dispatch_slack import dispatch_pending

    settings = get_settings()
    interval = settings.worker_poll_interval_seconds

    logger.info("slack_dispatcher_started", poll_interval=interval)

    async with httpx.AsyncClient() as client:
        while not _shutdown:
            try:
                async with get_standalone_session() as db:
                    processed = await dispatch_pending(
                        db, client=client, batch_size=settings.worker_batch_size
                    )
                    if processed > 0:
                        logger.info("slack_dispatcher_batch", processed=processed)
            except Exception as exc:
                logger.error("slack_dispatcher_error", error=str(exc))

            await asyncio.sleep(interval)


async def run_microsoft_sync() -> None:
    """Continuously pull users + groups from Microsoft Graph.

    Walks every connected Entra ID integration whose last sync is
    older than 4 hours (or null). Per-integration token refresh +
    additive upsert into DirectoryUser / DirectoryGroup.
    """
    import httpx
    from app.services.microsoft_sync import sync_pending

    settings = get_settings()
    interval = settings.worker_poll_interval_seconds

    logger.info("microsoft_sync_started", poll_interval=interval)

    async with httpx.AsyncClient() as client:
        while not _shutdown:
            try:
                async with get_standalone_session() as db:
                    processed = await sync_pending(
                        db, client=client, batch_size=settings.worker_batch_size
                    )
                    if processed > 0:
                        logger.info("microsoft_sync_batch", processed=processed)
            except Exception as exc:
                logger.error("microsoft_sync_error", error=str(exc))

            await asyncio.sleep(interval)


async def _run_continuous_sync(name: str, get_sync_fn) -> None:
    """Generic continuous-loop driver for Intune / Purview / Defender.

    `get_sync_fn` is a lazy callable that returns the provider's
    `sync_pending(db, *, client, batch_size)` function. Lazy so each
    sync service is only imported when its mode is selected.
    """
    import httpx

    sync_pending = get_sync_fn()
    settings = get_settings()
    interval = settings.worker_poll_interval_seconds
    logger.info(f"{name}_started", poll_interval=interval)

    async with httpx.AsyncClient() as client:
        while not _shutdown:
            try:
                async with get_standalone_session() as db:
                    processed = await sync_pending(
                        db, client=client, batch_size=settings.worker_batch_size
                    )
                    if processed > 0:
                        logger.info(f"{name}_batch", processed=processed)
            except Exception as exc:
                logger.error(f"{name}_error", error=str(exc))

            await asyncio.sleep(interval)


async def main() -> None:
    settings = get_settings()
    setup_logging(settings)
    await init_db()

    mode = os.environ.get("WORKER_MODE", "dispatcher")
    logger.info("worker_starting", mode=mode)

    try:
        if mode == "dispatcher":
            signal.signal(signal.SIGTERM, handle_shutdown)
            signal.signal(signal.SIGINT, handle_shutdown)
            await run_dispatcher()
        elif mode == "risk_analyzer":
            await run_risk_analyzer()
        elif mode == "correlation_engine":
            await run_correlation_engine()
        elif mode == "device_risk_engine":
            await run_device_risk_engine()
        elif mode == "slack_dispatcher":
            signal.signal(signal.SIGTERM, handle_shutdown)
            signal.signal(signal.SIGINT, handle_shutdown)
            await run_slack_dispatcher()
        elif mode == "microsoft_sync":
            signal.signal(signal.SIGTERM, handle_shutdown)
            signal.signal(signal.SIGINT, handle_shutdown)
            await run_microsoft_sync()
        elif mode == "intune_sync":
            signal.signal(signal.SIGTERM, handle_shutdown)
            signal.signal(signal.SIGINT, handle_shutdown)
            await _run_continuous_sync(
                "intune_sync",
                lambda: __import__(
                    "app.services.intune_sync", fromlist=["sync_pending"]
                ).sync_pending,
            )
        elif mode == "purview_sync":
            signal.signal(signal.SIGTERM, handle_shutdown)
            signal.signal(signal.SIGINT, handle_shutdown)
            await _run_continuous_sync(
                "purview_sync",
                lambda: __import__(
                    "app.services.purview_sync", fromlist=["sync_pending"]
                ).sync_pending,
            )
        elif mode == "defender_sync":
            signal.signal(signal.SIGTERM, handle_shutdown)
            signal.signal(signal.SIGINT, handle_shutdown)
            await _run_continuous_sync(
                "defender_sync",
                lambda: __import__(
                    "app.services.defender_sync", fromlist=["sync_pending"]
                ).sync_pending,
            )
        elif mode == "jamf_sync":
            signal.signal(signal.SIGTERM, handle_shutdown)
            signal.signal(signal.SIGINT, handle_shutdown)
            await _run_continuous_sync(
                "jamf_sync",
                lambda: __import__(
                    "app.services.jamf_sync", fromlist=["sync_pending"]
                ).sync_pending,
            )
        elif mode == "kandji_sync":
            signal.signal(signal.SIGTERM, handle_shutdown)
            signal.signal(signal.SIGINT, handle_shutdown)
            await _run_continuous_sync(
                "kandji_sync",
                lambda: __import__(
                    "app.services.kandji_sync", fromlist=["sync_pending"]
                ).sync_pending,
            )
        elif mode == "jumpcloud_sync":
            signal.signal(signal.SIGTERM, handle_shutdown)
            signal.signal(signal.SIGINT, handle_shutdown)
            await _run_continuous_sync(
                "jumpcloud_sync",
                lambda: __import__(
                    "app.services.jumpcloud_sync", fromlist=["sync_pending"]
                ).sync_pending,
            )
        elif mode == "handbook_reminder_dispatcher":
            # Periodically scan for users who haven't acknowledged
            # the current handbook version + DM them via Slack.
            # Uses INITIAL_GRACE_DAYS + 24h cooldown to avoid spam.
            from app.services.handbook_reminder import (
                dispatch_handbook_reminders,
            )
            import httpx

            settings = get_settings()
            interval = settings.worker_poll_interval_seconds
            dashboard_base_url = (
                getattr(settings, "atlas_dashboard_base_url", None)
                or "https://atlas.example.com"
            )
            logger.info(
                "handbook_reminder_started",
                poll_interval=interval,
                dashboard_base_url=dashboard_base_url,
            )

            signal.signal(signal.SIGTERM, handle_shutdown)
            signal.signal(signal.SIGINT, handle_shutdown)

            async with httpx.AsyncClient() as client:
                while not _shutdown:
                    try:
                        async with get_standalone_session() as db:
                            n = await dispatch_handbook_reminders(
                                db,
                                client=client,
                                batch_size=settings.worker_batch_size,
                                dashboard_base_url=dashboard_base_url,
                            )
                            if n > 0:
                                logger.info(
                                    "handbook_reminder_batch",
                                    dms_sent=n,
                                )
                    except Exception as exc:
                        logger.error(
                            "handbook_reminder_error",
                            error=str(exc),
                        )
                    await asyncio.sleep(interval)
        elif mode == "reattestation_reminder_dispatcher":
            # Periodically scan for OVERDUE reviews + DM each owner
            # via Slack. Uses 24h cooldown so we don't spam.
            from app.services.reattestation_reminder import (
                dispatch_overdue_reminders,
            )
            import httpx

            settings = get_settings()
            interval = settings.worker_poll_interval_seconds
            # The dashboard origin baked into DM links. Defaults to
            # the same backend host minus /api/v1 — operators can
            # override via env in v0.2.
            dashboard_base_url = (
                getattr(settings, "atlas_dashboard_base_url", None)
                or "https://atlas.example.com"
            )
            logger.info(
                "reattestation_reminder_started",
                poll_interval=interval,
                dashboard_base_url=dashboard_base_url,
            )

            signal.signal(signal.SIGTERM, handle_shutdown)
            signal.signal(signal.SIGINT, handle_shutdown)

            async with httpx.AsyncClient() as client:
                while not _shutdown:
                    try:
                        async with get_standalone_session() as db:
                            n = await dispatch_overdue_reminders(
                                db,
                                client=client,
                                batch_size=settings.worker_batch_size,
                                dashboard_base_url=dashboard_base_url,
                            )
                            if n > 0:
                                logger.info(
                                    "reattestation_reminder_batch",
                                    dms_sent=n,
                                )
                    except Exception as exc:
                        logger.error(
                            "reattestation_reminder_error",
                            error=str(exc),
                        )
                    await asyncio.sleep(interval)
        elif mode == "drift_signal_dispatcher":
            # Periodically scan Active use cases + apply the drift rule
            # engine. Each finding either augments an existing open
            # Review or creates a new one with drift_observed=True.
            # The reattestation_reminder_dispatcher then DMs the owner.
            from app.services.drift_signal_dispatcher import (
                dispatch_drift_signals,
            )

            settings = get_settings()
            # Drift rules are cheap (in-memory match against curated
            # signal sets), but we still throttle: once an hour is
            # plenty to catch a new deprecation announcement, and
            # avoids spamming an audit log on every poll cycle.
            interval = max(
                3600, settings.worker_poll_interval_seconds * 120
            )
            logger.info("drift_signal_dispatcher_started", poll_interval=interval)

            signal.signal(signal.SIGTERM, handle_shutdown)
            signal.signal(signal.SIGINT, handle_shutdown)

            while not _shutdown:
                try:
                    async with get_standalone_session() as db:
                        await dispatch_drift_signals(db)
                except Exception as exc:
                    logger.error("drift_signal_dispatcher_error", error=str(exc))
                await asyncio.sleep(interval)
        elif mode == "reattestation_enqueue":
            # Daily-ish loop that creates UseCaseReview rows for use
            # cases stale beyond the 90-day cadence. Calls the
            # service directly — no httpx client needed.
            from app.services.use_case_review_sync import enqueue_due_reviews

            settings = get_settings()
            # Poll less frequently than the MDM workers; once a day
            # is the natural cadence but we sleep `worker_poll_interval`
            # ×12 (~10 min default) to keep behaviour deterministic.
            interval = max(
                3600, settings.worker_poll_interval_seconds * 120
            )
            logger.info(
                "reattestation_enqueue_started", poll_interval=interval
            )
            while not _shutdown:
                try:
                    async with get_standalone_session() as db:
                        n = await enqueue_due_reviews(db)
                        if n > 0:
                            logger.info(
                                "reattestation_enqueue_batch",
                                processed=n,
                            )
                except Exception as exc:
                    logger.error(
                        "reattestation_enqueue_error", error=str(exc)
                    )
                await asyncio.sleep(interval)
        else:
            logger.error("unknown_worker_mode", mode=mode)
            sys.exit(1)
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
