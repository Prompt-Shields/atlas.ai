'use client';

/**
 * AI cost ledger API client and types.
 *
 * Mirrors the backend cost router (GET /api/v1/cost/*).
 *
 * Authentication: uses the same in-memory access token as `api.ts` /
 * `developer.ts`, read from sessionStorage on each request. Tenant
 * scoping is enforced server-side from the JWT.
 *
 * Decimal money fields may arrive as strings — callers should coerce
 * with Number() before formatting as USD.
 */

// ──────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────

export type CostBreakdownBy = 'provider' | 'model' | 'member';

/**
 * Where a cost figure came from. `vendor_reported` is an authoritative
 * (Actual) number billed by the provider; `derived_*` is atlas's own
 * (Estimated) computation from token usage; `mixed` is a roll-up that
 * blends both.
 */
export type CostSource =
  | 'vendor_reported'
  | 'derived_tokens'
  | 'derived_seats'
  | 'mixed';

export interface CostSummary {
  total_cost_usd: string | number;
  vendor_reported_usd: string | number;
  derived_usd: string | number;
  provisional_usd: string | number;
  active_connectors: number;
}

export interface CostTimeseriesPoint {
  date: string;
  cost_usd: string | number;
  is_provisional: boolean;
}

export interface CostBreakdownRow {
  key: string;
  cost_usd: string | number;
  cost_source: string;
}

export interface CostSyncResponse {
  records_upserted: number;
  since: string;
  until: string;
  status: string;
  error: string | null;
}

/** Window + optional provider filter shared by all cost queries. */
export interface CostQueryParams {
  since?: string;
  until?: string;
  provider?: string;
}

// ──────────────────────────────────────────────────────────────────────
// Fetch helper (mirrors lib/developer.ts)
// ──────────────────────────────────────────────────────────────────────

const API_URL =
  process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1';

function readAccessToken(): string | null {
  if (typeof window === 'undefined') return null;
  return sessionStorage.getItem('access_token');
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((init.headers as Record<string, string>) ?? {}),
  };

  const token = readAccessToken();
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const res = await fetch(`${API_URL}${path}`, { ...init, headers });

  if (!res.ok) {
    let detail: string;
    try {
      const body = (await res.json()) as { detail?: unknown };
      detail =
        typeof body.detail === 'string'
          ? body.detail
          : JSON.stringify(body.detail ?? body);
    } catch {
      detail = await res.text();
    }
    throw new Error(`${res.status} ${res.statusText}: ${detail}`);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ──────────────────────────────────────────────────────────────────────
// Date window helpers
// ──────────────────────────────────────────────────────────────────────

/** Format a Date as a YYYY-MM-DD string (UTC). */
function toDateParam(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/** Default window: the last `days` days, ending today (inclusive). */
export function defaultWindow(days = 30): { since: string; until: string } {
  const until = new Date();
  const since = new Date(until.getTime() - days * 864e5);
  return { since: toDateParam(since), until: toDateParam(until) };
}

function buildQuery(params: CostQueryParams): string {
  const { since, until } = defaultWindow();
  const q = new URLSearchParams();
  q.set('since', params.since || since);
  q.set('until', params.until || until);
  if (params.provider) q.set('provider', params.provider);
  return q.toString();
}

// ──────────────────────────────────────────────────────────────────────
// Cost API surface
// ──────────────────────────────────────────────────────────────────────

export function getCostSummary(
  params: CostQueryParams = {},
): Promise<CostSummary> {
  return request<CostSummary>(`/cost/summary?${buildQuery(params)}`);
}

export function getCostTimeseries(
  params: CostQueryParams = {},
): Promise<CostTimeseriesPoint[]> {
  return request<CostTimeseriesPoint[]>(`/cost/timeseries?${buildQuery(params)}`);
}

export function getCostBreakdown(
  by: CostBreakdownBy,
  params: CostQueryParams = {},
): Promise<CostBreakdownRow[]> {
  const q = new URLSearchParams(buildQuery(params));
  q.set('by', by);
  return request<CostBreakdownRow[]>(`/cost/breakdown?${q.toString()}`);
}

/** Per-integration manual "Sync now". */
export function syncCostIntegration(
  integrationId: string,
): Promise<CostSyncResponse> {
  return request<CostSyncResponse>(
    `/cost/integrations/${integrationId}/sync`,
    { method: 'POST' },
  );
}

export const costApi = {
  getCostSummary,
  getCostTimeseries,
  getCostBreakdown,
  syncCostIntegration,
};
