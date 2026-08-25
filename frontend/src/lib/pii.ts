'use client';

/**
 * PII Shield API client and types.
 *
 * Mirrors the backend PII router (POST /api/v1/pii/*, issue #245).
 * Stateless: `anonymize` returns a placeholder → original-text map to the
 * caller instead of persisting it; `reidentify` takes that same map back
 * in. There is nothing to fetch on mount — every call here is a direct
 * response to a user action (typing, sending, replying).
 *
 * Authentication: delegates to the shared ApiClient in `api.ts`, which
 * handles the Authorization header and 401 refresh-and-retry.
 */

import { api } from './api';

// ──────────────────────────────────────────────────────────────────────
// Types (mirror backend/app/schemas/pii.py)
// ──────────────────────────────────────────────────────────────────────

export type PIIClass =
  | 'EMAIL'
  | 'PHONE'
  | 'SSN'
  | 'CREDIT_CARD'
  | 'IP_ADDRESS'
  | 'API_KEY';

export interface PIISpan {
  pii_class: PIIClass;
  start: number;
  end: number;
  text: string;
  confidence: number;
}

export interface PIIDetectResponse {
  spans: PIISpan[];
}

export interface PIIAnonymizeResponse {
  redacted_text: string;
  placeholder_map: Record<string, string>;
  spans: PIISpan[];
}

export interface PIIReidentifyResponse {
  text: string;
}

// ──────────────────────────────────────────────────────────────────────
// PII API surface
// ──────────────────────────────────────────────────────────────────────

export function detectPii(text: string): Promise<PIIDetectResponse> {
  return api.request<PIIDetectResponse>('/pii/detect', {
    method: 'POST',
    body: JSON.stringify({ text }),
  });
}

export function anonymizePii(text: string): Promise<PIIAnonymizeResponse> {
  return api.request<PIIAnonymizeResponse>('/pii/anonymize', {
    method: 'POST',
    body: JSON.stringify({ text }),
  });
}

export function reidentifyPii(
  text: string,
  placeholderMap: Record<string, string>,
): Promise<PIIReidentifyResponse> {
  return api.request<PIIReidentifyResponse>('/pii/reidentify', {
    method: 'POST',
    body: JSON.stringify({ text, placeholder_map: placeholderMap }),
  });
}

/**
 * Client-side preview of the placeholder names `anonymize` would assign —
 * same per-class, per-occurrence numbering as the backend
 * (`[EMAIL_1]`, `[EMAIL_2]`, ...), computed from spans already returned by
 * `/pii/detect`. Used to show a live "this is what would be redacted"
 * preview while typing, before the user commits to `/pii/anonymize`. The
 * authoritative map for the actual round-trip always comes from the
 * backend response.
 */
export function previewPlaceholders(spans: PIISpan[]): Record<string, string> {
  const counts: Partial<Record<PIIClass, number>> = {};
  const map: Record<string, string> = {};
  for (const span of [...spans].sort((a, b) => a.start - b.start)) {
    const n = (counts[span.pii_class] ?? 0) + 1;
    counts[span.pii_class] = n;
    map[`[${span.pii_class}_${n}]`] = span.text;
  }
  return map;
}

export const piiApi = {
  detectPii,
  anonymizePii,
  reidentifyPii,
  previewPlaceholders,
};
