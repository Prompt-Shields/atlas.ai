'use client';

/**
 * Agent control tower API client and types (M1, issue #236).
 *
 * Mirrors the backend Pydantic schemas in `app/schemas/agent_control.py`
 * and the `/api/v1/agent-control` router landed in #235: health and
 * lifecycle are derived server-side from discovery data + persisted
 * overrides, this module just wires the read + action calls.
 *
 * `AgentCloudProvider`/`AgentDiscoveryStatus` are duplicated from
 * `lib/aispm/agent-discovery.ts` (#234, not yet merged as of this issue) —
 * once that module lands on main, re-export from there instead.
 */

import { api } from '../api';

// ──────────────────────────────────────────────────────────────────────
// Enums (string unions mirroring the backend enums)
// ──────────────────────────────────────────────────────────────────────

export type AgentCloudProvider = 'aws_bedrock_agentcore' | 'azure_ai_foundry' | 'gcp_knowledge_catalog';

export type AgentDiscoveryStatus = 'shadow' | 'pending' | 'approved' | 'dismissed';

export type AgentHealth = 'healthy' | 'degraded' | 'unhealthy';

export type AgentLifecycle = 'unmonitored' | 'provisioning' | 'active' | 'paused' | 'quarantined';

export type AgentControlAction = 'pause' | 'quarantine' | 'toggle-guardrail';

// ──────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────

export interface AgentControlState {
  id: string;
  provider: AgentCloudProvider;
  name: string;
  registry: string;
  discovery_status: AgentDiscoveryStatus;
  health: AgentHealth;
  lifecycle: AgentLifecycle;
  guardrail_enabled: boolean;
  last_action: AgentControlAction | null;
  last_action_at: string | null;
  last_seen_at: string;
}

export interface AgentControlListResponse {
  agents: AgentControlState[];
}

// ──────────────────────────────────────────────────────────────────────
// Client
// ──────────────────────────────────────────────────────────────────────

const BASE = '/agent-control';

export const agentControlApi = {
  list: (): Promise<AgentControlListResponse> => api.request<AgentControlListResponse>(BASE),

  applyAction: (id: string, action: AgentControlAction): Promise<AgentControlState> =>
    api.request<AgentControlState>(`${BASE}/${id}/action`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    }),
};
