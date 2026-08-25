"""Correlation Engine Agent — correlates risks and creates action plans."""

from __future__ import annotations

import json
import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm_client import estimate_cost, invoke_llm
from app.agents.prompts import CORRELATION_SYSTEM_PROMPT, CORRELATION_USER_TEMPLATE
from app.config import get_settings
from app.models.correlation import CorrelationActionPlan
from app.models.dispatch import DispatchEvent, OutboxMessage
from app.models.llm_usage import LLMUsageRecord
from app.models.risk import RiskMitigation
from app.models.settings import TenantSetting

logger = structlog.get_logger()


async def correlate_risks(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    org_id: uuid.UUID,
    batch_id: str | None = None,
    job_id: str | None = None,
) -> list[CorrelationActionPlan]:
    """Correlate risk/mitigation records and create action plans.

    Behavior depends on tenant setting:
    - research_all_data=True: correlate against ALL accumulated data
    - research_all_data=False: correlate only the latest batch
    """
    settings = get_settings()

    # Check tenant setting for research mode
    ts_result = await db.execute(select(TenantSetting).where(TenantSetting.tenant_id == tenant_id))
    tenant_settings = ts_result.scalar_one_or_none()
    research_all = (
        tenant_settings.research_all_data if tenant_settings else settings.research_all_data
    )

    # Fetch risks to correlate
    query = (
        select(RiskMitigation)
        .where(
            RiskMitigation.tenant_id == tenant_id,
            RiskMitigation.org_id == org_id,
            RiskMitigation.processing_status == "completed",
        )
        .order_by(RiskMitigation.created_at.desc())
    )

    if not research_all and batch_id:
        query = query.where(RiskMitigation.batch_id == batch_id)
    elif not research_all:
        query = query.limit(settings.worker_batch_size)

    result = await db.execute(query)
    risks = list(result.scalars().all())

    if len(risks) < 2:
        logger.info("correlation_insufficient_risks", count=len(risks))
        return []

    logger.info(
        "correlation_start",
        risk_count=len(risks),
        research_all=research_all,
        job_id=job_id,
    )

    # Format risks for the LLM prompt
    risk_records_text = "\n\n".join(
        f"Risk ID: {r.id}\n"
        f"Title: {r.risk_title}\n"
        f"Category: {r.risk_category}\n"
        f"Severity: {r.risk_severity}\n"
        f"Score: {r.risk_score}\n"
        f"Description: {r.risk_description}\n"
        f"Mitigation: {r.mitigation_description}"
        for r in risks
    )

    user_prompt = CORRELATION_USER_TEMPLATE.format(
        research_mode="ALL accumulated data" if research_all else "Latest batch only",
        risk_records=risk_records_text,
    )

    parsed, usage_info = await invoke_llm(
        system_prompt=CORRELATION_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        tenant_id=tenant_id,
        org_id=org_id,
        job_id=job_id,
        operation_type="correlation",
    )

    if "error" in parsed:
        logger.warning("correlation_error", error=parsed["error"])
        return []

    # Create correlation record
    risk_ids = [str(r.id) for r in risks]
    is_test_data = any(r.is_test_data for r in risks)

    correlation = CorrelationActionPlan(
        tenant_id=tenant_id,
        org_id=org_id,
        risk_mitigation_ids=json.dumps(risk_ids),
        batch_id=batch_id,
        correlation_title=parsed.get("correlation_title", "Unknown"),
        correlation_summary=parsed.get("correlation_summary", ""),
        correlation_type=parsed.get("correlation_type", "pattern"),
        overall_risk_score=float(parsed.get("overall_risk_score", 0)),
        confidence_score=float(parsed.get("confidence_score", 0)),
        action_plan_title=parsed.get("action_plan_title", "N/A"),
        action_plan_description=parsed.get("action_plan_description", ""),
        action_steps=json.dumps(parsed.get("action_steps", [])),
        priority=parsed.get("priority", "low"),
        estimated_effort=parsed.get("estimated_effort"),
        citations=json.dumps(parsed.get("citations", [])),
        reasoning=parsed.get("reasoning", ""),
        model_used=settings.azure_openai_deployment_name,
        created_by_job=job_id,
        is_test_data=is_test_data,
    )
    db.add(correlation)
    await db.flush()

    # Track LLM usage
    cost = estimate_cost(
        settings.azure_openai_deployment_name,
        usage_info["prompt_tokens"],
        usage_info["completion_tokens"],
    )
    usage = LLMUsageRecord(
        tenant_id=tenant_id,
        org_id=org_id,
        job_id=job_id,
        model_name=settings.azure_openai_deployment_name,
        prompt_tokens=usage_info["prompt_tokens"],
        completion_tokens=usage_info["completion_tokens"],
        total_tokens=usage_info["total_tokens"],
        estimated_cost_usd=cost,
        operation_type="correlation",
    )
    db.add(usage)

    # Create dispatch event
    dispatch_payload = {
        "correlation_id": str(correlation.id),
        "title": correlation.correlation_title,
        "summary": correlation.correlation_summary,
        "priority": correlation.priority,
        "risk_score": correlation.overall_risk_score,
        "action_plan": parsed.get("action_steps", []),
    }

    dispatch_event = DispatchEvent(
        tenant_id=tenant_id,
        org_id=org_id,
        correlation_id=correlation.id,
        event_type="correlation_created",
        payload=json.dumps(dispatch_payload),
        severity=correlation.priority,
        is_test_data=is_test_data,
    )
    db.add(dispatch_event)

    # Create outbox message for durable delivery
    outbox_msg = OutboxMessage(
        tenant_id=tenant_id,
        event_type="correlation_created",
        aggregate_id=correlation.id,
        aggregate_type="correlation",
        payload=json.dumps(dispatch_payload),
    )
    db.add(outbox_msg)

    await db.flush()

    logger.info(
        "correlation_complete",
        correlation_id=str(correlation.id),
        priority=correlation.priority,
    )
    return [correlation]
