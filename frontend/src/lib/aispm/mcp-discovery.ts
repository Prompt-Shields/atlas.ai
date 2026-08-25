'use client';

/**
 * MCP server discovery API client and types (M3, issue #241).
 *
 * Mirrors the backend Pydantic schemas in `app/schemas/mcp_discovery.py`
 * and the `/api/v1/discover/mcp` router: collector observations are fused
 * into scored candidates server-side (see
 * `app/services/mcp_discovery_service.py`'s `correlate()` / `score_server()`),
 * so the client only needs to render what comes back. Delegates
 * auth/refresh to the shared ApiClient in `api.ts`, matching
 * `lib/aispm/agent-discovery.ts`.
 */

import { api } from '../api';

// ──────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────

export type McpDiscoveryStatus = 'candidate' | 'promoted' | 'dismissed';

export interface DiscoveredMcpServer {
  id: string;
  canonical_key: string;
  name: string;
  host: string | null;
  transport: string;
  description: string | null;
  sources: string[];
  observation_count: number;
  confidence_score: number;
  status: McpDiscoveryStatus;
  promoted_asset_id: string | null;
  last_observed_at: string;
}

export interface McpDiscoveryList {
  servers: DiscoveredMcpServer[];
}

export interface McpDiscoveryScanResult {
  scanned_observations: number;
  created: number;
  updated: number;
  servers: DiscoveredMcpServer[];
}

export interface McpServerStatusUpdate {
  status: McpDiscoveryStatus;
}

export interface McpServerPromoteResult {
  server: DiscoveredMcpServer;
  asset_id: string;
}

// ──────────────────────────────────────────────────────────────────────
// Client
// ──────────────────────────────────────────────────────────────────────

const BASE = '/discover/mcp';

export const mcpDiscoveryApi = {
  list: (): Promise<McpDiscoveryList> => api.request<McpDiscoveryList>(BASE),

  scan: (): Promise<McpDiscoveryScanResult> =>
    api.request<McpDiscoveryScanResult>(`${BASE}/scan`, { method: 'POST' }),

  promote: (id: string): Promise<McpServerPromoteResult> =>
    api.request<McpServerPromoteResult>(`${BASE}/${id}/promote`, { method: 'POST' }),

  updateStatus: (id: string, body: McpServerStatusUpdate): Promise<DiscoveredMcpServer> =>
    api.request<DiscoveredMcpServer>(`${BASE}/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),

  dismiss: (id: string): Promise<DiscoveredMcpServer> =>
    mcpDiscoveryApi.updateStatus(id, { status: 'dismissed' }),
};
