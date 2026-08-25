"""Testing service — super admin E2E pipeline execution and test data purge."""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import get_adapter
from app.agents import correlation_engine, risk_analyzer
from app.models.blob import BlobRecord
from app.models.correlation import CorrelationActionPlan
from app.models.dispatch import DispatchEvent, OutboxMessage
from app.models.risk import RiskMitigation
from app.models.user import APIKey, User, UserRole
from app.services.audit import log_audit_event

logger = structlog.get_logger()

TEST_BLOB_CONTENT = """
Ardent Harbor Logistics is a global supply chain coordination firm with regional hubs in North America, Europe, and Southeast Asia. Over the past decade, its rapid digital transformation—spanning fleet telemetry, warehouse robotics, and predictive analytics—has introduced efficiencies alongside a widening spectrum of security exposures. While the organization has avoided any publicly disclosed breaches, internal reviews and near-incidents have underscored structural weaknesses that require sustained attention.
A recurring issue has been fragmented access control. Business divisions historically onboarded software platforms independently, leading to inconsistent authentication practices and duplicated user accounts across systems. Some legacy portals still permit password-only authentication, and temporary vendor accounts are not always time-bound. To address this, Ardent Harbor initiated a company-wide identity harmonization project. All applications are being federated through a central identity broker, adaptive multi-factor authentication is enforced based on risk signals, and privileged access is granted through just-in-time elevation workflows. Dormant accounts are flagged automatically if inactive beyond defined thresholds, and managers must re-certify team access rights every quarter.
Cloud misconfigurations have also surfaced as a vulnerability. During a routine assessment, security analysts discovered that a staging environment exposed internal APIs due to overly permissive security group rules. Though exploitation was not observed, the finding exposed weaknesses in change governance. Engineering has since mandated standardized deployment pipelines using pre-approved configuration modules. Any infrastructure change triggers automated compliance checks against policy baselines, and high-risk changes require dual authorization. Continuous monitoring dashboards provide real-time visibility into configuration drift across accounts.
Social engineering remains a credible threat, particularly in procurement and accounts payable functions where external communications are constant. A recent attempt to redirect vendor payments via fraudulent email narrowly failed due to manual verification by a finance analyst. Recognizing the pattern, leadership introduced mandatory call-back verification procedures for payment detail changes. Additionally, contextual phishing simulations now mimic real vendor scenarios rather than generic templates. Email authentication protocols (SPF, DKIM, DMARC) were hardened to reduce spoofing, and a reporting button was embedded directly into the email client to streamline incident reporting.
Within distribution centers, industrial control systems (ICS) coordinate conveyor belts and automated sorting equipment. Some of these systems run proprietary firmware that cannot be readily updated. Previously, they shared network pathways with administrative systems to simplify remote troubleshooting. A security review determined this architecture increased lateral movement risk. The network topology was subsequently redesigned so that operational equipment resides within tightly restricted segments, accessible only through monitored bastion hosts. Remote vendor maintenance sessions now require temporary credentials and are logged for audit purposes.
Data exfiltration risk has been amplified by the organization’s emphasis on analytics. Analysts frequently aggregate large volumes of shipment and customer metadata to refine forecasting models. In the past, extracts were sometimes copied to unmanaged devices for convenience. To curtail this practice, Ardent Harbor deployed endpoint monitoring tools that detect bulk data transfers and unsanctioned external storage usage. At the same time, a sanctioned analytics enclave was created, offering scalable compute resources within a controlled boundary. Access requests to sensitive datasets are tied to ticketing workflows and expire automatically after project completion.
Third-party dependencies present another layer of exposure. The firm integrates with numerous shipping partners, customs brokers, and SaaS route-optimization providers. Previously, onboarding focused primarily on API compatibility and uptime guarantees. Following a supplier breach that disrupted data feeds, Ardent Harbor formalized its vendor risk program. Critical partners are now classified by data sensitivity and operational impact. Security addenda require disclosure of material incidents within specified timeframes, and high-impact vendors must furnish independent audit attestations. Integration tokens are rotated regularly and scoped to least privilege.
Physical safeguards have also evolved. In one warehouse audit, it was observed that shared workstations remained logged in during shift changes, and visitor access badges were not consistently collected. While no malicious activity was detected, the lax practices indicated opportunity for misuse. The facilities team implemented automatic session timeouts on shared terminals and introduced badge-return checkpoints at exit points. Surveillance coverage was expanded in sensitive storage areas, and periodic spot checks reinforce adherence to clean desk and device-locking policies.
On the software engineering front, Ardent Harbor’s development teams previously maintained disparate repositories with inconsistent review standards. In some cases, security testing occurred only at release milestones. To improve resilience, security scanning tools are now integrated directly into the continuous integration pipeline, identifying vulnerable libraries and insecure coding patterns before deployment. Merge approvals require at least one reviewer outside the originating team for critical services. Credentials are stored in a centralized vault, and applications retrieve secrets dynamically rather than embedding them in configuration files.
Incident readiness has matured as well. An earlier malware outbreak in a regional office exposed confusion regarding escalation thresholds and external communications. Post-incident analysis led to clearer delineation of responsibilities within the crisis management framework. Playbooks now outline containment steps for various threat categories, and annual simulations involve executive leadership to rehearse decision-making under pressure. Backup communication channels—including secure messaging platforms independent of corporate email—are maintained to ensure coordination during widespread outages.
Compliance complexity is heightened by cross-border trade regulations and data protection laws affecting shipment records and employee information. Inconsistent data labeling previously made it difficult to determine which records were subject to specific jurisdictional controls. The compliance team introduced a standardized data taxonomy embedded within core systems. Automated rules restrict cross-region replication of certain categories without documented approval, and retention schedules are enforced programmatically to reduce unnecessary data accumulation.
Resilience gaps were identified during a disaster recovery review. Some regional hubs relied entirely on SaaS providers’ native redundancy without verifying restoration procedures. To mitigate this, Ardent Harbor instituted independent backups for mission-critical datasets and conducts periodic recovery drills to validate recovery time objectives. Cloud snapshots are stored in immutable storage tiers to guard against ransomware tampering. Business continuity plans are reviewed annually and updated to reflect evolving dependencies.
Collectively, these initiatives illustrate an incremental shift from reactive patchwork fixes toward a more integrated security posture. By distributing controls across identity management, network architecture, vendor governance, workforce awareness, and operational resilience, Ardent Harbor Logistics aims to reduce systemic fragility while preserving the agility required in a competitive logistics landscape.
"""


async def run_e2e_test_pipeline(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    correlation_id: str | None = None,
) -> dict:
    """Execute full E2E test pipeline:
    1. Blob insertion (marked as TEST)
    2. Risk analysis (agentic processing)
    3. Correlation
    4. Dispatch + storage

    All data is marked with is_test_data=True for safe cleanup.
    """
    job_id = f"test-e2e-{uuid.uuid4()}"
    results: dict = {"job_id": job_id, "steps": []}

    # Audit: test run started
    await log_audit_event(
        db,
        event_type="test.run_started",
        action="create",
        actor_id=user_id,
        tenant_id=tenant_id,
        org_id=org_id,
        details={"job_id": job_id},
        correlation_id=correlation_id,
    )

    try:
        # Step 1: Ingest test blob
        adapter = get_adapter("manual")
        blobs = await adapter.ingest(
            db,
            tenant_id=tenant_id,
            org_id=org_id,
            created_by=user_id,
            batch_id=job_id,
            is_test_data=True,
            content=TEST_BLOB_CONTENT,
            title="E2E Test - Security Assessment",
            source_id="test-blob-001",
        )
        results["steps"].append(
            {
                "step": "blob_ingestion",
                "status": "success",
                "blob_ids": [str(b.id) for b in blobs],
            }
        )

        # Step 2: Run risk analysis
        risks = await risk_analyzer.process_batch(db, batch_id=job_id, job_id=job_id)
        results["steps"].append(
            {
                "step": "risk_analysis",
                "status": "success",
                "risk_count": len(risks),
                "risk_ids": [str(r.id) for r in risks],
            }
        )

        # Step 3: Run correlation
        correlations = await correlation_engine.correlate_risks(
            db,
            tenant_id=tenant_id,
            org_id=org_id,
            batch_id=job_id,
            job_id=job_id,
        )
        results["steps"].append(
            {
                "step": "correlation",
                "status": "success",
                "correlation_count": len(correlations),
                "correlation_ids": [str(c.id) for c in correlations],
            }
        )

        results["status"] = "success"

    except Exception as exc:
        logger.error("test_pipeline_error", error=str(exc), job_id=job_id)
        results["status"] = "failed"
        results["error"] = str(exc)

    # Audit: test run completed
    await log_audit_event(
        db,
        event_type="test.run_completed",
        action="update",
        actor_id=user_id,
        tenant_id=tenant_id,
        org_id=org_id,
        details=results,
        correlation_id=correlation_id,
    )

    return results


async def purge_test_data(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
) -> dict:
    """Purge all data marked as test data. Requires confirmation.

    If tenant_id is provided, only purge test data for that tenant.
    If None (super admin), purge ALL test data.
    """
    counts: dict[str, int] = {}

    # Audit: purge started
    await log_audit_event(
        db,
        event_type="test.purge_started",
        action="delete",
        actor_id=user_id,
        tenant_id=tenant_id,
        details={"scope": "tenant" if tenant_id else "global"},
        correlation_id=correlation_id,
    )

    # Delete in correct order (foreign key dependencies)
    for model, name in [
        (OutboxMessage, "outbox_messages"),
        (DispatchEvent, "dispatch_events"),
        (CorrelationActionPlan, "correlations"),
        (RiskMitigation, "risks"),
        (BlobRecord, "blobs"),
    ]:
        query = delete(model).where(model.is_test_data.is_(True))
        if tenant_id:
            query = query.where(model.tenant_id == tenant_id)
        result = await db.execute(query)
        counts[name] = result.rowcount  # type: ignore[assignment]

    # Clean test users and API keys
    for model, name in [
        (APIKey, "api_keys"),
        (UserRole, "user_roles"),
    ]:
        query = delete(model).where(
            model.id.in_(select(model.id).join(User).where(User.is_test_data.is_(True)))
        )
        if tenant_id:
            query = delete(model).where(
                model.user_id.in_(
                    select(User.id).where(User.is_test_data.is_(True), User.tenant_id == tenant_id)
                )
            )
        result = await db.execute(query)
        counts[name] = result.rowcount  # type: ignore[assignment]

    # Delete test users
    user_query = delete(User).where(User.is_test_data.is_(True))
    if tenant_id:
        user_query = user_query.where(User.tenant_id == tenant_id)
    result = await db.execute(user_query)
    counts["users"] = result.rowcount  # type: ignore[assignment]

    await db.flush()

    # Audit: purge completed
    await log_audit_event(
        db,
        event_type="test.purge_completed",
        action="delete",
        actor_id=user_id,
        tenant_id=tenant_id,
        details=counts,
        correlation_id=correlation_id,
    )

    logger.info("test_data_purged", counts=counts)
    return counts
