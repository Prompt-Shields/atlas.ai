'use client';

/**
 * Defender for Cloud Apps import API client (M3, issue #243).
 *
 * Mirrors the backend Pydantic schemas in `app/schemas/defender_import.py`
 * and the `/api/v1/discover/defender-import` router: a pasted export is
 * extracted + enriched server-side (see
 * `app/services/defender_import_service.py`'s `KNOWN_APPS` catalog), so
 * the client only needs to render what comes back. Delegates auth/refresh
 * to the shared ApiClient in `api.ts`, matching `lib/aispm/mcp-discovery.ts`.
 */

import { api } from '../api';

// ──────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────

export type DefenderImportStatus = 'pending' | 'accepted' | 'dismissed';

export interface DiscoveredDefenderApp {
  id: string;
  canonical_name: string;
  name: string;
  category: string;
  users_count: number;
  data_volume_mb: number;
  risk_score: number;
  certifications: string[];
  compliance_status: Record<string, string>;
  status: DefenderImportStatus;
  promoted_vendor_id: string | null;
  imported_at: string;
}

export interface DefenderImportList {
  apps: DiscoveredDefenderApp[];
}

export interface DefenderImportRequest {
  raw_text?: string | null;
}

export interface DefenderImportResult {
  matched_lines: number;
  created: number;
  updated: number;
  apps: DiscoveredDefenderApp[];
}

export interface DefenderAppStatusUpdate {
  status: DefenderImportStatus;
}

export interface DefenderAppAcceptRequest {
  department?: string;
}

export interface DefenderAppAcceptResult {
  app: DiscoveredDefenderApp;
  vendor_id: string;
}

// ──────────────────────────────────────────────────────────────────────
// Sample export (paste-box placeholder / demo convenience)
// ──────────────────────────────────────────────────────────────────────

export const SAMPLE_DEFENDER_EXPORT = `ChatGPT Enterprise — 312 active users — 1.2 TB transferred
Grammarly Business — 178 active users — 340 GB transferred
Notion AI — 96 active users — 78 GB transferred
GitHub Copilot — 54 active users — 12 GB transferred
Otter.ai — 23 active users — 9 GB transferred
`;

// ──────────────────────────────────────────────────────────────────────
// Client
// ──────────────────────────────────────────────────────────────────────

const BASE = '/discover/defender-import';

export const defenderImportApi = {
  list: (): Promise<DefenderImportList> => api.request<DefenderImportList>(BASE),

  import: (payload: DefenderImportRequest): Promise<DefenderImportResult> =>
    api.request<DefenderImportResult>(`${BASE}/import`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  accept: (id: string, payload: DefenderAppAcceptRequest = {}): Promise<DefenderAppAcceptResult> =>
    api.request<DefenderAppAcceptResult>(`${BASE}/${id}/accept`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  updateStatus: (id: string, body: DefenderAppStatusUpdate): Promise<DiscoveredDefenderApp> =>
    api.request<DiscoveredDefenderApp>(`${BASE}/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),

  dismiss: (id: string): Promise<DiscoveredDefenderApp> =>
    defenderImportApi.updateStatus(id, { status: 'dismissed' }),
};
