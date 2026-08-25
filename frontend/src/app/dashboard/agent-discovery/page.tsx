'use client';

// ─────────────────────────────────────────────────────────────────────
// Agent Discovery — /dashboard/agent-discovery  (M1, issue #234)
//
// Lists discovered cloud AI agents (AWS Bedrock AgentCore, Azure AI
// Foundry, GCP Knowledge Catalog) grouped by provider and status, with
// approve/dismiss actions. Wired to /api/v1/agent-discovery via
// lib/aispm/agent-discovery.ts (backend + collectors landed in #233).
// ─────────────────────────────────────────────────────────────────────

import { useCallback, useEffect, useState } from 'react';
import {
  agentDiscoveryApi,
  type AgentDiscoveryList,
  type DiscoveredAgent,
} from '@/lib/aispm/agent-discovery';
import { DiscoveryConsole } from '@/components/spm/agent-discovery/discovery-console';

const EMPTY_LIST: AgentDiscoveryList = { shadow: [], pending: [], approved: [], dismissed: [] };

export default function AgentDiscoveryPage() {
  const [list, setList] = useState<AgentDiscoveryList>(EMPTY_LIST);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setList(await agentDiscoveryApi.list());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load discovered agents');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const runScan = async () => {
    setScanning(true);
    setError(null);
    try {
      await agentDiscoveryApi.scan();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Scan failed');
    } finally {
      setScanning(false);
    }
  };

  const decide = async (agent: DiscoveredAgent, action: 'approve' | 'dismiss') => {
    setBusyId(agent.id);
    setError(null);
    try {
      await agentDiscoveryApi[action](agent.id);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : `Failed to ${action} agent`);
    } finally {
      setBusyId(null);
    }
  };

  const totalAgents = list.shadow.length + list.pending.length + list.approved.length + list.dismissed.length;

  return (
    <div className="p-8">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold">Agent Discovery</h1>
          <p className="text-gray-600">Cloud AI agents found across your registries, awaiting review.</p>
        </div>
        <button
          className="rounded bg-indigo-600 px-3 py-2 text-sm text-white hover:bg-indigo-500 disabled:opacity-50"
          disabled={scanning}
          onClick={runScan}
        >
          {scanning ? 'Scanning…' : 'Run scan'}
        </button>
      </div>

      {error && <p className="mb-4 text-sm text-red-600">{error}</p>}

      {loading ? (
        <div className="py-12 text-center text-gray-500">Loading discovered agents…</div>
      ) : totalAgents === 0 ? (
        <div className="py-12 text-center text-gray-500">
          No agents discovered yet. Run a scan to pull the latest agents from your cloud registries.
        </div>
      ) : (
        <DiscoveryConsole
          list={list}
          busyId={busyId}
          onApprove={(agent) => void decide(agent, 'approve')}
          onDismiss={(agent) => void decide(agent, 'dismiss')}
        />
      )}
    </div>
  );
}
