"""LLM client wrapper with usage tracking and guardrails.

- Uses Semantic Kernel *prompt functions* for a chunked (map-reduce) extraction pipeline,
  which is much more robust for large blob inputs.
- Runs per-chunk extraction with strict JSON output, then a deterministic merge pass.
- Aggregates token usage across all calls.
- Includes a JSON-repair fallback pass if any chunk or final output is invalid JSON.

Notes:
- Azure OpenAI chat endpoints typically support temperature + top_p; "top_k" is not a
  standard parameter there. If your backend exposes top_k, wire it in similarly.
- Setting temperature=0 and very low top_p pushes determinism, but no LLM can be
  guaranteed "fully factual" beyond the source text + prompt constraints.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import structlog
from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.open_ai import AzureChatCompletion
from semantic_kernel.connectors.ai.open_ai.prompt_execution_settings.azure_chat_prompt_execution_settings import (
    AzureChatPromptExecutionSettings,
)
from semantic_kernel.functions.kernel_function_from_prompt import KernelFunctionFromPrompt
from semantic_kernel.prompt_template import PromptTemplateConfig

from app.config import get_settings

logger = structlog.get_logger()

SERVICE_ID = "azure_openai"


def create_kernel() -> Kernel:
    """Create a Semantic Kernel instance with Azure OpenAI."""
    settings = get_settings()
    kernel = Kernel()

    service = AzureChatCompletion(
        deployment_name=settings.azure_openai_deployment_name,
        endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
        service_id=SERVICE_ID,
    )
    kernel.add_service(service)
    return kernel


def get_execution_settings(*, max_tokens: int) -> AzureChatPromptExecutionSettings:
    """Get LLM execution settings with strict guardrails.

    Determinism knobs:
    - temperature=0.0
    - top_p=0.01 (very low nucleus sampling)

    Structured output:
    - response_format=json_object
    """
    return AzureChatPromptExecutionSettings(
        service_id=SERVICE_ID,
        temperature=0.0,
        top_p=0.01,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )


# ---------------------------
# Chunking helpers
# ---------------------------


@dataclass(frozen=True)
class Chunk:
    idx: int
    text: str


def _chunk_text_by_chars(
    text: str, *, chunk_chars: int = 12_000, overlap_chars: int = 800
) -> list[Chunk]:
    """Simple char-based chunker (tokenizer-free). Good enough for large prompts.

    - overlap helps keep entities crossing boundaries consistent
    - tune chunk_chars based on your model/context + your prompt size
    """
    if chunk_chars <= 0:
        return [Chunk(0, text)]

    chunks: list[Chunk] = []
    start = 0
    idx = 0
    n = len(text)
    while start < n:
        end = min(n, start + chunk_chars)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(Chunk(idx=idx, text=chunk))
            idx += 1
        if end >= n:
            break
        start = max(0, end - overlap_chars)
    return chunks


def _safe_json_loads(s: str) -> dict[str, Any] | None:
    try:
        return json.loads(s.strip())
    except Exception:
        return None


def _extract_usage(message_content) -> dict[str, int]:
    """Extract usage info from SK message content metadata when present."""
    usage_info = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    meta = getattr(message_content, "metadata", None) or {}
    usage = meta.get("usage", {}) if isinstance(meta, dict) else {}

    # SK sometimes surfaces usage as a dict, sometimes an object with attrs
    if hasattr(usage, "prompt_tokens"):
        usage_info["prompt_tokens"] = int(getattr(usage, "prompt_tokens", 0) or 0)
        usage_info["completion_tokens"] = int(getattr(usage, "completion_tokens", 0) or 0)
    elif isinstance(usage, dict):
        usage_info["prompt_tokens"] = int(usage.get("prompt_tokens", 0) or 0)
        usage_info["completion_tokens"] = int(usage.get("completion_tokens", 0) or 0)

    usage_info["total_tokens"] = usage_info["prompt_tokens"] + usage_info["completion_tokens"]
    return usage_info


def _sum_usage(usages: Iterable[dict[str, int]]) -> dict[str, int]:
    total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for u in usages:
        total["prompt_tokens"] += int(u.get("prompt_tokens", 0) or 0)
        total["completion_tokens"] += int(u.get("completion_tokens", 0) or 0)
        total["total_tokens"] += int(u.get("total_tokens", 0) or 0)
    return total


# ---------------------------
# SK prompt functions (map / reduce / repair)
# ---------------------------


def _build_chunk_extractor_fn() -> KernelFunctionFromPrompt:
    """
    Chunk-level extractor.
    Produces JSON object *only* based on the chunk, leaving unknowns as null.
    """
    template = """
{{$system_prompt}}

You will be given PART {{$chunk_index}} of {{$chunk_total}} of a larger text.
Extract ONLY facts explicitly stated in this part.
If a field isn't supported by this part alone, set it to null (or [] for list fields if truly absent).
Do not infer, guess, or use outside knowledge.
Return ONLY valid JSON (no markdown, no commentary).

USER INPUT (PART {{$chunk_index}}/{{$chunk_total}}):
{{$chunk_text}}
""".strip()

    cfg = PromptTemplateConfig(
        name="chunk_extractor",
        description="Extract structured data from a single chunk of a large text",
        template=template,
        template_format="semantic-kernel",
        execution_settings={
            SERVICE_ID: {
                "temperature": 0.0,
                "top_p": 0.01,
                "max_tokens": 1800,
                "response_format": {"type": "json_object"},
            }
        },
    )
    return KernelFunctionFromPrompt(
        function_name="extract_from_chunk",
        plugin_name="extraction",
        prompt_template_config=cfg,
    )


def _build_merger_fn() -> KernelFunctionFromPrompt:
    """
    Merge pass:
    - Combine chunk JSONs into ONE final JSON matching the system prompt constraints.
    - Prefer explicit facts; resolve conflicts by selecting the most consistently supported value.
    - Do not invent missing values.
    """
    template = """
{{$system_prompt}}

You will be given JSON objects extracted from multiple chunks of the SAME source text.
Merge them into ONE final JSON object following the same schema.
Rules:
- Do NOT add fields outside the schema.
- Do NOT guess. If conflicting values exist and cannot be resolved from the provided objects, set the field to null.
- When merging lists (e.g., items), deduplicate identical entries.
Return ONLY valid JSON.

CHUNK JSON OBJECTS:
{{$partials_json}}
""".strip()

    cfg = PromptTemplateConfig(
        name="merge_extractions",
        description="Merge multiple partial JSON extractions into a single final JSON",
        template=template,
        template_format="semantic-kernel",
        execution_settings={
            SERVICE_ID: {
                "temperature": 0.0,
                "top_p": 0.01,
                "max_tokens": 4096,
                "response_format": {"type": "json_object"},
            }
        },
    )
    return KernelFunctionFromPrompt(
        function_name="merge_partials",
        plugin_name="extraction",
        prompt_template_config=cfg,
    )


def _build_json_repair_fn() -> KernelFunctionFromPrompt:
    """
    Repair pass:
    If the model returned non-JSON, ask it to re-emit valid JSON only.
    """
    template = """
{{$system_prompt}}

The following model output was supposed to be JSON but is invalid.
Re-emit it as valid JSON ONLY, strictly following the schema and rules.
If content cannot be mapped, use null.
No markdown, no commentary.

INVALID OUTPUT:
{{$invalid_output}}
""".strip()

    cfg = PromptTemplateConfig(
        name="json_repair",
        description="Repair invalid JSON model output into valid JSON only",
        template=template,
        template_format="semantic-kernel",
        execution_settings={
            SERVICE_ID: {
                "temperature": 0.0,
                "top_p": 0.01,
                "max_tokens": 1200,
                "response_format": {"type": "json_object"},
            }
        },
    )
    return KernelFunctionFromPrompt(
        function_name="repair_json",
        plugin_name="extraction",
        prompt_template_config=cfg,
    )


# ---------------------------
# Public API (same interface)
# ---------------------------


async def invoke_llm(
    *,
    system_prompt: str,
    user_prompt: str,
    tenant_id: uuid.UUID,
    org_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    job_id: str | None = None,
    operation_type: str = "unknown",
) -> tuple[dict[str, Any], dict[str, int]]:
    """Invoke the LLM and track usage.

    Returns:
        (parsed_response, usage_info)
        usage_info = {"prompt_tokens": int, "completion_tokens": int, "total_tokens": int}
    """
    kernel = create_kernel()

    chunk_fn = _build_chunk_extractor_fn()
    merge_fn = _build_merger_fn()
    repair_fn = _build_json_repair_fn()

    # 1) Chunk the *user_prompt* (assumed to contain/represent a large blob)
    chunks = _chunk_text_by_chars(user_prompt, chunk_chars=12_000, overlap_chars=800)

    # If it's small enough, we still run through the same pipeline (1 chunk).
    chunk_usages: list[dict[str, int]] = []
    partials: list[dict[str, Any]] = []

    # 2) MAP: per-chunk extraction
    for ch in chunks:
        try:
            result = await kernel.invoke(
                chunk_fn,
                system_prompt=system_prompt,
                chunk_index=str(ch.idx + 1),
                chunk_total=str(len(chunks)),
                chunk_text=ch.text,
            )
            raw = str(result)
            parsed = _safe_json_loads(raw)

            # JSON repair fallback
            if parsed is None:
                repaired = await kernel.invoke(
                    repair_fn,
                    system_prompt=system_prompt,
                    invalid_output=raw[:8000],
                )
                raw2 = str(repaired)
                parsed = _safe_json_loads(raw2)

            if parsed is None:
                logger.error("llm_invalid_json_chunk", chunk_index=ch.idx, operation=operation_type)
                parsed = {"error": "Invalid JSON response", "raw": raw[:1000]}

            # Try to collect usage (best-effort)
            # Note: SK may not attach usage to FunctionResult; we attempt from inner message if present.
            # If unavailable, it remains zeros.
            msg = getattr(result, "value", None)  # differs across versions
            if msg:
                chunk_usages.append(_extract_usage(msg))
            else:
                chunk_usages.append({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})

            partials.append(parsed)

        except Exception as e:
            logger.exception(
                "llm_chunk_invoke_failed", chunk_index=ch.idx, operation=operation_type, err=str(e)
            )
            chunk_usages.append({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
            partials.append({"error": "chunk_invoke_failed", "chunk_index": ch.idx})

    # 3) REDUCE: merge partial JSONs deterministically
    partials_json = json.dumps(partials, ensure_ascii=False)

    try:
        merged_result = await kernel.invoke(
            merge_fn,
            system_prompt=system_prompt,
            partials_json=partials_json,
        )
        merged_raw = str(merged_result)
        merged = _safe_json_loads(merged_raw)

        # Final repair fallback
        if merged is None:
            repaired_final = await kernel.invoke(
                repair_fn,
                system_prompt=system_prompt,
                invalid_output=merged_raw[:8000],
            )
            merged = _safe_json_loads(str(repaired_final))

        if merged is None:
            logger.error(
                "llm_invalid_json_final", operation=operation_type, content_preview=merged_raw[:200]
            )
            merged = {"error": "Invalid JSON response", "raw": merged_raw[:1000]}

        # Usage for merge (best-effort)
        merge_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        msg2 = getattr(merged_result, "value", None)
        if msg2:
            merge_usage = _extract_usage(msg2)

        usage_info = _sum_usage([*chunk_usages, merge_usage])

    except Exception as e:
        logger.exception("llm_merge_invoke_failed", operation=operation_type, err=str(e))
        merged = {"error": "merge_invoke_failed"}
        usage_info = _sum_usage(chunk_usages)

    logger.info(
        "llm_invocation",
        operation=operation_type,
        org_id=str(org_id) if org_id else None,
        user_id=str(user_id) if user_id else None,
        job_id=str(job_id) if job_id else None,
        tenant_id=str(tenant_id),
        prompt_tokens=usage_info["prompt_tokens"],
        completion_tokens=usage_info["completion_tokens"],
        chunks=len(chunks),
    )

    return merged, usage_info


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate cost in USD based on model and token counts.

    Pricing as of 2025 for Azure OpenAI (approximate).
    """
    pricing = {
        "gpt-4o": {"input": 0.0025, "output": 0.01},  # per 1K tokens
        "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
        "gpt-4": {"input": 0.03, "output": 0.06},
        "gpt-35-turbo": {"input": 0.0005, "output": 0.0015},
    }

    rates = pricing.get(model, pricing["gpt-4o"])
    cost = (prompt_tokens / 1000 * rates["input"]) + (completion_tokens / 1000 * rates["output"])
    return round(cost, 6)
