'use client';

/**
 * Microsoft Sentinel connector API client and types (M5, issue #247).
 *
 * Mirrors the backend Pydantic schemas in `app/schemas/sentinel.py` and the
 * `/api/v1/integrations/sentinel` router: v1 stores the workspace/table +
 * event-type mapping and serves a seeded event stream — no live call to
 * Azure Monitor. Delegates auth/refresh to the shared ApiClient in
 * `api.ts`, matching `lib/aispm/mcp-discovery.ts`.
 */

import { api } from '../api';
import type { IntegrationCardResponse } from '../types';

// ──────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────

export type SentinelEventType =
  | 'Redacted'
  | 'Anonymised'
  | 'Blocked'
  | 'Coached'
  | 'BiasFlagged';

export type SentinelSeverity = 'Low' | 'Medium' | 'High';

export interface SentinelConnectRequest {
  workspace_name: string;
  table_name?: string;
  enabled_event_types?: SentinelEventType[];
}

export interface SentinelEvent {
  time_generated: string;
  event_id: string;
  user: string;
  ai_tool: string;
  is_shadow_ai: boolean;
  event_type: SentinelEventType;
  sensitive_type: string | null;
  severity: SentinelSeverity;
  detail: string;
  prompt_hash: string;
}

export interface SentinelEventStreamResponse {
  connected: boolean;
  workspace_name: string | null;
  table_name: string | null;
  enabled_event_types: SentinelEventType[];
  events: SentinelEvent[];
}

export const SENTINEL_EVENT_TYPES: SentinelEventType[] = [
  'Redacted',
  'Anonymised',
  'Blocked',
  'Coached',
  'BiasFlagged',
];

// ──────────────────────────────────────────────────────────────────────
// Client
// ──────────────────────────────────────────────────────────────────────

const BASE = '/integrations/sentinel';

export const sentinelApi = {
  connect: (body: SentinelConnectRequest): Promise<IntegrationCardResponse> =>
    api.request<IntegrationCardResponse>(`${BASE}/connect`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  events: (): Promise<SentinelEventStreamResponse> =>
    api.request<SentinelEventStreamResponse>(`${BASE}/events`),
};
