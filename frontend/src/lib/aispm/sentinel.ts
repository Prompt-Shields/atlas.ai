'use client';

/**
 * Microsoft Sentinel connector API client and types.
 *
 * Mirrors the backend Pydantic schemas in `app/schemas/sentinel.py` and the
 * `/api/v1/integrations/sentinel` router. Two modes share one integration:
 * connecting with only a workspace label gives the seeded preview stream,
 * while supplying the Azure Monitor coordinates starts live forwarding.
 * Delegates auth/refresh to the shared ApiClient in `api.ts`, matching
 * `lib/aispm/mcp-discovery.ts`.
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
  // Azure Monitor coordinates. All-or-nothing: the backend rejects a partial
  // set (422) because a half-filled config would read as connected while
  // forwarding nothing. Omit them all for the seeded-preview mode.
  azure_tenant_id?: string;
  client_id?: string;
  client_secret?: string;
  dce_url?: string;
  dcr_immutable_id?: string;
  stream_name?: string;
}

export type SentinelDeadLetterStatus = 'PENDING' | 'REPLAYED' | 'DISCARDED';

export interface SentinelForwarderStatus {
  connected: boolean;
  forwarder_configured: boolean;
  workspace_name: string | null;
  table_name: string | null;
  stream_name: string | null;
  dcr_immutable_id: string | null;
  enabled_event_types: SentinelEventType[];
  events_forwarded: number;
  events_skipped: number;
  batches_sent: number;
  batches_dead_lettered: number;
  pending_dead_letters: number;
  last_run_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
}

export interface SentinelForwardRunResponse {
  events_read: number;
  events_forwarded: number;
  events_skipped: number;
  batches_sent: number;
  batches_dead_lettered: number;
  error: string | null;
}

export interface SentinelDeadLetter {
  id: string;
  status: SentinelDeadLetterStatus;
  reason: string;
  http_status: number | null;
  error_detail: string | null;
  event_count: number;
  first_event_id: string | null;
  last_event_id: string | null;
  attempts: number;
  created_at: string;
  replayed_at: string | null;
}

export interface SentinelDeadLetterListResponse {
  items: SentinelDeadLetter[];
  total: number;
}

export interface SentinelReplayResponse {
  replayed: boolean;
  status: SentinelDeadLetterStatus;
  detail: string | null;
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
  // False while the tenant is in seeded-preview mode.
  forwarder_configured: boolean;
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

  status: (): Promise<SentinelForwarderStatus> =>
    api.request<SentinelForwarderStatus>(`${BASE}/status`),

  /** Run the forwarder once — "Forward now" during setup. */
  forward: (): Promise<SentinelForwardRunResponse> =>
    api.request<SentinelForwardRunResponse>(`${BASE}/forward`, { method: 'POST' }),

  deadLetters: (status?: SentinelDeadLetterStatus): Promise<SentinelDeadLetterListResponse> =>
    api.request<SentinelDeadLetterListResponse>(
      status ? `${BASE}/dead-letters?status=${status}` : `${BASE}/dead-letters`,
    ),

  replayDeadLetter: (id: string): Promise<SentinelReplayResponse> =>
    api.request<SentinelReplayResponse>(`${BASE}/dead-letters/${id}/replay`, {
      method: 'POST',
    }),
};
