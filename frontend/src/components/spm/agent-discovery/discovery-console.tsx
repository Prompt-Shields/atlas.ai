// ─────────────────────────────────────────────────────────────────────
// DiscoveryConsole — status filter + provider panes for agent discovery.
//
// Owns no data fetching: the page loads the AgentDiscoveryList and
// approve/dismiss handlers and hands them down. Filters the flattened
// agent list by status tab, then groups the result by provider for
// CloudPane.
// ─────────────────────────────────────────────────────────────────────

'use client';

import { useMemo, useState } from 'react';
import type {
  AgentCloudProvider,
  AgentDiscoveryList,
  AgentDiscoveryStatus,
  DiscoveredAgent,
} from '@/lib/aispm/agent-discovery';
import { CloudPane, PROVIDER_LABELS } from './cloud-pane';
import { MetaAgentBar } from './meta-agent-bar';

const STATUS_TABS: { value: AgentDiscoveryStatus | 'all'; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'shadow', label: 'Shadow' },
  { value: 'pending', label: 'Pending' },
  { value: 'approved', label: 'Approved' },
  { value: 'dismissed', label: 'Dismissed' },
];

const PROVIDER_ORDER: AgentCloudProvider[] = [
  'aws_bedrock_agentcore',
  'azure_ai_foundry',
  'gcp_knowledge_catalog',
];

interface DiscoveryConsoleProps {
  list: AgentDiscoveryList;
  busyId: string | null;
  onApprove: (agent: DiscoveredAgent) => void;
  onDismiss: (agent: DiscoveredAgent) => void;
}

export function DiscoveryConsole({ list, busyId, onApprove, onDismiss }: DiscoveryConsoleProps) {
  const [statusFilter, setStatusFilter] = useState<AgentDiscoveryStatus | 'all'>('all');

  const all = useMemo(
    () => [...list.shadow, ...list.pending, ...list.approved, ...list.dismissed],
    [list],
  );

  const filtered = useMemo(
    () => (statusFilter === 'all' ? all : all.filter((a) => a.status === statusFilter)),
    [all, statusFilter],
  );

  const byProvider = useMemo(() => {
    const groups = new Map<AgentCloudProvider, DiscoveredAgent[]>();
    for (const agent of filtered) {
      const group = groups.get(agent.provider) ?? [];
      group.push(agent);
      groups.set(agent.provider, group);
    }
    return groups;
  }, [filtered]);

  return (
    <div className="space-y-6">
      <MetaAgentBar
        shadow={list.shadow.length}
        pending={list.pending.length}
        approved={list.approved.length}
        dismissed={list.dismissed.length}
      />

      <div className="flex gap-2 border-b border-gray-200">
        {STATUS_TABS.map((tab) => (
          <button
            key={tab.value}
            className={`border-b-2 px-3 py-2 text-sm font-medium ${
              statusFilter === tab.value
                ? 'border-indigo-600 text-indigo-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
            onClick={() => setStatusFilter(tab.value)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {filtered.length === 0 ? (
        <div className="py-12 text-center text-gray-500">No agents in this view.</div>
      ) : (
        <div className="space-y-8">
          {PROVIDER_ORDER.filter((p) => byProvider.has(p)).map((provider) => (
            <CloudPane
              key={provider}
              provider={provider}
              agents={byProvider.get(provider) ?? []}
              busyId={busyId}
              onApprove={onApprove}
              onDismiss={onDismiss}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export { PROVIDER_LABELS };
