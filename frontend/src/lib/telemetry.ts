'use client';

/**
 * Prompt-telemetry API client — dashboard read layer.
 *
 * Mirrors the backend Pydantic schemas in app/schemas/telemetry.py.
 * Field names are snake_case to match the backend verbatim (same convention
 * as developer.ts / app/schemas/developer.py).
 *
 * Authentication: same in-memory access-token mechanism as developer.ts —
 * Bearer JWT read from sessionStorage, set during login via api.ts.
 *
 * These endpoints are READ-ONLY for the dashboard.  Ingestion is a separate
 * surface: clients (Safari extension, macOS widget, SDK) write via
 * POST /telemetry/prompt-events authenticated with an X-API-Key, never this
 * app's session token.
 */

// ──────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────

export type PromptEventSource = 'safari_extension' | 'macos_widget' | 'sdk';
export type BreakdownDimension = 'app' | 'pii_category' | 'source';

export interface PromptActivitySummary {
  total_prompts: number;
  total_violations: number;
  violation_rate: number;
  active_devices: number;
  active_sources: number;
}

export interface PromptActivityBucket {
  date: string;
  prompts: number;
  violations: number;
}

export interface PromptActivityBreakdownRow {
  key: string;
  prompts: number;
  violations: number;
}

export interface PromptActivityFilters {
  since?: string;
  until?: string;
  source?: PromptEventSource;
  app_id?: string;
}

// ──────────────────────────────────────────────────────────────────────
// Fetch helper (mirrors developer.ts — private to this module)
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
// Query-param builder
// ──────────────────────────────────────────────────────────────────────

function buildQuery(
  params: Record<string, string | undefined>,
): string {
  const q = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') {
      q.set(key, value);
    }
  }
  const s = q.toString();
  return s ? `?${s}` : '';
}

// ──────────────────────────────────────────────────────────────────────
// Telemetry API surface
// ──────────────────────────────────────────────────────────────────────

export const telemetryApi = {
  /**
   * GET /telemetry/prompt-activity/summary
   *
   * Aggregate totals for the dashboard header cards.
   * All filter params are optional.
   */
  summary: (filters?: PromptActivityFilters): Promise<PromptActivitySummary> => {
    const qs = buildQuery({
      since: filters?.since,
      until: filters?.until,
      source: filters?.source,
      app_id: filters?.app_id,
    });
    return request<PromptActivitySummary>(
      `/telemetry/prompt-activity/summary${qs}`,
    );
  },

  /**
   * GET /telemetry/prompt-activity/timeseries
   *
   * Daily buckets ordered by date ascending.
   * Empty days are not fabricated — the backend only returns days with data.
   */
  timeseries: (
    filters?: PromptActivityFilters,
  ): Promise<{ buckets: PromptActivityBucket[] }> => {
    const qs = buildQuery({
      since: filters?.since,
      until: filters?.until,
      source: filters?.source,
      app_id: filters?.app_id,
    });
    return request<{ buckets: PromptActivityBucket[] }>(
      `/telemetry/prompt-activity/timeseries${qs}`,
    );
  },

  /**
   * GET /telemetry/prompt-activity/breakdown?by=app|pii_category|source
   *
   * Rows sorted by prompts (or violations for pii_category) descending.
   * `by` is required; filters are optional.
   */
  breakdown: (
    by: BreakdownDimension,
    filters?: PromptActivityFilters,
  ): Promise<{ by: string; rows: PromptActivityBreakdownRow[] }> => {
    const qs = buildQuery({
      by,
      since: filters?.since,
      until: filters?.until,
      source: filters?.source,
      app_id: filters?.app_id,
    });
    return request<{ by: string; rows: PromptActivityBreakdownRow[] }>(
      `/telemetry/prompt-activity/breakdown${qs}`,
    );
  },
};
