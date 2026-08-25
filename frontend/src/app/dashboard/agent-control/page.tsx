'use client';

// ─────────────────────────────────────────────────────────────────────
// Agent Control — /dashboard/agent-control  (M1, issue #236, Beta)
//
// Control-tower UI over discovered agents: KPI strip, fleet table, and
// a per-agent drawer with runtime health / guardrail posture / lifecycle
// and pause/quarantine/toggle-guardrail actions. Wired to
// /api/v1/agent-control via lib/aispm/agent-control.ts (backend landed
// in #235).
// ─────────────────────────────────────────────────────────────────────

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  agentControlApi,
  type AgentControlAction,
  type AgentControlState,
} from '@/lib/aispm/agent-control';
import { KpiStrip } from '@/components/spm/agent-control/kpi-strip';
import { FleetTable } from '@/components/spm/agent-control/fleet-table';
import { AgentDrawer } from '@/components/spm/agent-control/agent-drawer';

export default function AgentControlPage() {
  const [agents, setAgents] = useState<AgentControlState[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await agentControlApi.list();
      setAgents(res.agents);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load agent control state');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const selected = useMemo(
    () => agents.find((a) => a.id === selectedId) ?? null,
    [agents, selectedId],
  );

  const applyAction = async (agent: AgentControlState, action: AgentControlAction) => {
    setBusy(true);
    setError(null);
    try {
      const updated = await agentControlApi.applyAction(agent.id, action);
      setAgents((prev) => prev.map((a) => (a.id === updated.id ? updated : a)));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Action failed');
    } finally {
      setBusy(false);
    }
  };

  const kpis = useMemo(() => {
    const total = agents.length;
    const healthy = agents.filter((a) => a.health === 'healthy').length;
    const guardrailsOff = agents.filter((a) => !a.guardrail_enabled).length;
    const underAction = agents.filter(
      (a) => a.lifecycle === 'paused' || a.lifecycle === 'quarantined',
    ).length;
    return { total, healthy, guardrailsOff, underAction };
  }, [agents]);

  return (
    <div className="p-8">
      <div className="mb-6">
        <div className="flex items-center gap-2">
          <h1 className="text-3xl font-semibold">Agent Control</h1>
          <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-700 ring-1 ring-inset ring-indigo-600/20">
            Beta
          </span>
        </div>
        <p className="text-gray-600">
          Runtime health, guardrail posture, and lifecycle for your governed AI agents.
        </p>
      </div>

      {error && <p className="mb-4 text-sm text-red-600">{error}</p>}

      {loading ? (
        <div className="py-12 text-center text-gray-500">Loading agent control tower…</div>
      ) : (
        <div className="space-y-6">
          <KpiStrip {...kpis} />
          <FleetTable agents={agents} onSelect={(agent) => setSelectedId(agent.id)} />
        </div>
      )}

      <AgentDrawer
        agent={selected}
        busy={busy}
        onClose={() => setSelectedId(null)}
        onAction={(agent, action) => void applyAction(agent, action)}
      />
    </div>
  );
}
