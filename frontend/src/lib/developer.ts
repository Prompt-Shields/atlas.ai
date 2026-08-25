'use client';

/**
 * Developer API client and types.
 *
 * Mirrors the backend Pydantic schemas in app/schemas/developer.py.
 *
 * Authentication: delegates to the shared ApiClient in `api.ts`, which
 * handles the Authorization header and 401 refresh-and-retry.
 */

import { api } from './api';

// ──────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────

export type DeveloperScope =
  | 'events:write'
  | 'events:read'
  | 'event_types:write'
  | 'event_types:read';

export type EventSeverity =
  | 'debug'
  | 'info'
  | 'warning'
  | 'error'
  | 'critical';

export const ALL_SCOPES: DeveloperScope[] = [
  'events:write',
  'events:read',
  'event_types:write',
  'event_types:read',
];

export const ALL_SEVERITIES: EventSeverity[] = [
  'debug',
  'info',
  'warning',
  'error',
  'critical',
];

export interface DeveloperAPIKey {
  id: string;
  name: string;
  description: string | null;
  key_prefix: string;
  is_active: boolean;
  scopes: DeveloperScope[];
  created_at: string;
  updated_at: string;
}

export interface DeveloperAPIKeyCreateResponse extends DeveloperAPIKey {
  full_key: string;
}

export interface EventDefinition {
  id: string;
  name: string;
  description: string | null;
  schema: Record<string, unknown> | null;
  is_enabled: boolean;
  default_severity: EventSeverity;
  created_at: string;
  updated_at: string;
}

export interface DeveloperEvent {
  id: string;
  event_type: string;
  severity: EventSeverity;
  occurred_at: string;
  payload: Record<string, unknown>;
  source: string | null;
  session_id: string | null;
  user_external_id: string | null;
  correlation_id: string | null;
  occurrences: number;
  created_at: string;
}

export interface EventListResponse {
  events: DeveloperEvent[];
  total: number;
  has_more: boolean;
  next_cursor: string | null;
}

// ──────────────────────────────────────────────────────────────────────
// Developer API surface
// ──────────────────────────────────────────────────────────────────────

export const developerApi = {
  // ── API Keys ─────────────────────────────────────────────────────
  listKeys: (includeInactive = false) =>
    api.request<{ keys: DeveloperAPIKey[]; total: number }>(
      `/developer/keys?include_inactive=${includeInactive}`,
    ),

  createKey: (body: {
    name: string;
    description?: string | null;
    scopes?: DeveloperScope[];
  }) =>
    api.request<DeveloperAPIKeyCreateResponse>('/developer/keys', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  revokeKey: (keyId: string) =>
    api.request<void>(`/developer/keys/${keyId}`, { method: 'DELETE' }),

  updateKeyScopes: (keyId: string, scopes: DeveloperScope[]) =>
    api.request<DeveloperAPIKey>(`/developer/keys/${keyId}/scopes`, {
      method: 'PATCH',
      body: JSON.stringify({ scopes }),
    }),

  // ── Event Types ──────────────────────────────────────────────────
  listEventTypes: (includeDisabled = false) =>
    api.request<{ definitions: EventDefinition[]; total: number }>(
      `/developer/event-types?include_disabled=${includeDisabled}`,
    ),

  createEventType: (body: {
    name: string;
    description?: string | null;
    schema?: Record<string, unknown> | null;
    default_severity?: EventSeverity;
  }) =>
    api.request<EventDefinition>('/developer/event-types', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  updateEventType: (
    id: string,
    body: Partial<{
      description: string | null;
      schema: Record<string, unknown> | null;
      is_enabled: boolean;
      default_severity: EventSeverity;
    }>,
  ) =>
    api.request<EventDefinition>(`/developer/event-types/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),

  deleteEventType: (id: string) =>
    api.request<void>(`/developer/event-types/${id}`, { method: 'DELETE' }),

  // ── Events ───────────────────────────────────────────────────────
  listEvents: (params?: {
    event_type?: string;
    severity?: EventSeverity;
    source?: string;
    session_id?: string;
    correlation_id?: string;
    since?: string;
    until?: string;
    limit?: number;
    after?: string;
  }) => {
    const q = new URLSearchParams();
    if (params) {
      for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== null && value !== '') {
          q.set(key, String(value));
        }
      }
    }
    return api.request<EventListResponse>(
      `/developer/events${q.toString() ? `?${q}` : ''}`,
    );
  },

  getEvent: (id: string) =>
    api.request<DeveloperEvent>(`/developer/events/${id}`),
};
