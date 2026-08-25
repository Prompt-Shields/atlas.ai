'use client';

/**
 * Agent discovery API client and types (M1, issue #233).
 *
 * Mirrors the backend Pydantic schemas in `app/schemas/agent_discovery.py`
 * and the `/api/v1/agent-discovery` router. The backend always has data to
 * return once a scan has run once (the seed collectors are the fallback
 * until real cloud SDK collectors land — see
 * `app/services/agent_discovery_collectors.py`), so no separate client-side
 * seed data is needed here. Delegates auth/refresh to the shared ApiClient
 * in `api.ts`.
 */

import { api } from '../api';

// ──────────────────────────────────────────────────────────────────────
// Enums (string unions mirroring the backend enums)
// ──────────────────────────────────────────────────────────────────────

export type AgentCloudProvider =
  | 'aws_bedrock_agentcore'
  | 'azure_ai_foundry'
  | 'gcp_knowledge_catalog';

export type AgentDiscoveryStatus = 'shadow' | 'pending' | 'approved' | 'dismissed';

// ──────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────

export interface DiscoveredAgent {
  id: string;
  provider: AgentCloudProvider;
  external_id: string;
  name: string;
  registry: string;
  runtime: string | null;
  region: string | null;
  description: string | null;
  status: AgentDiscoveryStatus;
  owner_user_id: string | null;
  last_seen_at: string;
}

export interface AgentDiscoveryList {
  shadow: DiscoveredAgent[];
  pending: DiscoveredAgent[];
  approved: DiscoveredAgent[];
  dismissed: DiscoveredAgent[];
}

export interface AgentDiscoveryScanResult {
  scanned: number;
  created: number;
  updated: number;
  agents: DiscoveredAgent[];
}

export interface AgentStatusUpdate {
  status: AgentDiscoveryStatus;
}

// ──────────────────────────────────────────────────────────────────────
// Client
// ──────────────────────────────────────────────────────────────────────

const BASE = '/agent-discovery';

export const agentDiscoveryApi = {
  list: (): Promise<AgentDiscoveryList> => api.request<AgentDiscoveryList>(BASE),

  scan: (): Promise<AgentDiscoveryScanResult> =>
    api.request<AgentDiscoveryScanResult>(`${BASE}/scan`, { method: 'POST' }),

  updateStatus: (id: string, body: AgentStatusUpdate): Promise<DiscoveredAgent> =>
    api.request<DiscoveredAgent>(`${BASE}/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),

  approve: (id: string): Promise<DiscoveredAgent> =>
    agentDiscoveryApi.updateStatus(id, { status: 'approved' }),

  dismiss: (id: string): Promise<DiscoveredAgent> =>
    agentDiscoveryApi.updateStatus(id, { status: 'dismissed' }),
};
