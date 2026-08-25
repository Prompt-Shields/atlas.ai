"""Risk Analyzer Agent — processes blob records and generates risk assessments."""

from __future__ import annotations

import json

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm_client import estimate_cost, invoke_llm
from app.agents.prompts import RISK_ANALYSIS_SYSTEM_PROMPT, RISK_ANALYSIS_USER_TEMPLATE
from app.config import get_settings
from app.models.blob import BlobRecord
from app.models.llm_usage import LLMUsageRecord
from app.models.risk import RiskMitigation

logger = structlog.get_logger()


def _allocate_usage(usage_info: dict, n: int) -> list[dict]:
    """
    Split one invoke_llm() usage blob across n results so we can preserve the
    per-blob LLMUsageRecord behavior as a drop-in replacement.
    """
    if n <= 0:
        return []

    prompt = int(usage_info.get("prompt_tokens") or 0)
    completion = int(usage_info.get("completion_tokens") or 0)
    total = int(usage_info.get("total_tokens") or (prompt + completion))

    def split(x: int) -> list[int]:
        base = x // n
        rem = x % n
        # distribute remainder to the first `rem` items
        return [base + (1 if i < rem else 0) for i in range(n)]

    p = split(prompt)
    c = split(completion)
    t = split(total)

    return [
        {"prompt_tokens": p[i], "completion_tokens": c[i], "total_tokens": t[i]} for i in range(n)
    ]


def _normalize_results(parsed: object) -> list[dict]:
    """
    Accept common multi-result shapes:
      - list[dict]
      - {"results": list[dict]}
      - single dict (treated as one result)
    """
    if isinstance(parsed, list):
        return [x for x in parsed if isinstance(x, dict)]
    if isinstance(parsed, dict):
        if isinstance(parsed.get("results"), list):
            return [x for x in parsed["results"] if isinstance(x, dict)]
        return [parsed]
    return []


async def process_batch(
    db: AsyncSession,
    batch_id: str | None = None,
    job_id: str | None = None,
    limit: int = 100,
) -> list[RiskMitigation]:
    """Process a batch of pending blob records through risk analysis.

    Args:
        db: Database session
        batch_id: Optional specific batch to process
        job_id: Job identifier for idempotency
        limit: Max records to process
    """
    query = (
        select(BlobRecord)
        .where(BlobRecord.processing_status == "pending")
        .order_by(BlobRecord.created_at.asc())
        .limit(limit)
    )
    if batch_id:
        query = query.where(BlobRecord.batch_id == batch_id)

    result = await db.execute(query)
    blobs = list(result.scalars().all())

    if not blobs:
        logger.info("risk_analyzer_no_pending", batch_id=batch_id)
        return []

    logger.info("risk_analyzer_start", count=len(blobs), batch_id=batch_id, job_id=job_id)
    settings = get_settings()
    risks: list[RiskMitigation] = []

    # To preserve correctness when multiple tenants/orgs are present in the same query,
    # we invoke once per (tenant_id, org_id) group (still concatenated within each group).
    groups: dict[tuple[str, str], list[BlobRecord]] = {}
    for b in blobs:
        groups.setdefault((str(b.tenant_id), str(b.org_id)), []).append(b)

    # Mark all as processing up-front (same net behavior, fewer flushes)
    for blob in blobs:
        blob.processing_status = "processing"
    await db.flush()

    for (tenant_id, org_id), group_blobs in groups.items():
        try:
            # Build ONE user prompt that contains all blobs for this tenant/org.
            # No manual truncation/chunking; invoke_llm is responsible for that.
            user_prompt = "\n\n".join(
                RISK_ANALYSIS_USER_TEMPLATE.format(
                    blob_id=str(blob.id),
                    title=blob.title or "Untitled",
                    source_type=blob.source_type,
                    content=blob.content,
                )
                for blob in group_blobs
            )

            parsed, usage_info = await invoke_llm(
                system_prompt=RISK_ANALYSIS_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                tenant_id=group_blobs[0].tenant_id,
                org_id=group_blobs[0].org_id,
                job_id=job_id,
                operation_type="risk_analysis",
            )

            # If the model returns a top-level error, fail the entire group.
            if isinstance(parsed, dict) and "error" in parsed:
                err = parsed.get("error")
                logger.warning(
                    "risk_analysis_error_group",
                    tenant_id=tenant_id,
                    org_id=org_id,
                    error=err,
                    count=len(group_blobs),
                )
                for blob in group_blobs:
                    blob.processing_status = "failed"
                continue

            results = _normalize_results(parsed)

            # Index results by blob_id when present
            by_blob_id: dict[str, dict] = {}
            for item in results:
                bid = item.get("blob_id") or item.get("id")
                if bid is not None:
                    by_blob_id[str(bid)] = item

            usage_splits = _allocate_usage(usage_info or {}, len(group_blobs))

            for i, blob in enumerate(group_blobs):
                item = by_blob_id.get(str(blob.id))

                # If we can't match a result to a blob, mark it failed (safer than guessing).
                if not item:
                    logger.warning(
                        "risk_analysis_missing_result",
                        blob_id=str(blob.id),
                        tenant_id=tenant_id,
                        org_id=org_id,
                    )
                    blob.processing_status = "failed"
                    continue

                if "error" in item:
                    logger.warning(
                        "risk_analysis_error",
                        blob_id=str(blob.id),
                        error=item.get("error"),
                    )
                    blob.processing_status = "failed"
                    continue

                risk = RiskMitigation(
                    tenant_id=blob.tenant_id,
                    org_id=blob.org_id,
                    blob_id=blob.id,
                    batch_id=blob.batch_id,
                    risk_title=item.get("risk_title", "Unknown"),
                    risk_description=item.get("risk_description", ""),
                    risk_category=item.get("risk_category", "unknown"),
                    risk_severity=item.get("risk_severity", "informational"),
                    risk_likelihood=item.get("risk_likelihood", "rare"),
                    risk_score=float(item.get("risk_score", 0) or 0),
                    confidence_score=float(item.get("confidence_score", 0) or 0),
                    mitigation_title=item.get("mitigation_title", "N/A"),
                    mitigation_description=item.get("mitigation_description", ""),
                    mitigation_steps=json.dumps(item.get("mitigation_steps", [])),
                    citations=json.dumps(item.get("citations", [])),
                    model_used=settings.azure_openai_deployment_name,
                    processing_status="completed",
                    created_by_job=job_id,
                    is_test_data=blob.is_test_data,
                )
                db.add(risk)

                # Preserve per-blob usage records by apportioning the single call's tokens.
                split = (
                    usage_splits[i]
                    if i < len(usage_splits)
                    else {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                )
                cost = estimate_cost(
                    settings.azure_openai_deployment_name,
                    split["prompt_tokens"],
                    split["completion_tokens"],
                )
                usage = LLMUsageRecord(
                    tenant_id=blob.tenant_id,
                    org_id=blob.org_id,
                    job_id=job_id,
                    model_name=settings.azure_openai_deployment_name,
                    prompt_tokens=split["prompt_tokens"],
                    completion_tokens=split["completion_tokens"],
                    total_tokens=split["total_tokens"],
                    estimated_cost_usd=cost,
                    operation_type="risk_analysis",
                )
                db.add(usage)

                blob.processing_status = "processed"
                risks.append(risk)

        except Exception as exc:
            logger.error(
                "risk_analysis_group_error",
                tenant_id=tenant_id,
                org_id=org_id,
                error=str(exc),
            )
            for blob in group_blobs:
                blob.processing_status = "failed"

    await db.flush()
    logger.info("risk_analyzer_complete", processed=len(risks), failed=len(blobs) - len(risks))
    return risks
