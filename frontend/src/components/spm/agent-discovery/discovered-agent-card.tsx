// ─────────────────────────────────────────────────────────────────────
// DiscoveredAgentCard — a single discovered AI agent row.
//
// Shows the agent's identity (name, registry, runtime, region), a status
// badge, and approve/dismiss actions for agents still awaiting a
// decision (shadow/pending). Approved/dismissed agents render read-only.
// ─────────────────────────────────────────────────────────────────────

import type { AgentDiscoveryStatus, DiscoveredAgent } from '@/lib/aispm/agent-discovery';

const STATUS_LABELS: Record<AgentDiscoveryStatus, string> = {
  shadow: 'Shadow',
  pending: 'Pending review',
  approved: 'Approved',
  dismissed: 'Dismissed',
};

const STATUS_TONES: Record<AgentDiscoveryStatus, string> = {
  shadow: 'bg-red-50 text-red-700 ring-red-600/20',
  pending: 'bg-amber-50 text-amber-700 ring-amber-600/20',
  approved: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
  dismissed: 'bg-gray-100 text-gray-600 ring-gray-500/20',
};

export function StatusBadge({ status }: { status: AgentDiscoveryStatus }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${STATUS_TONES[status]}`}>
      {STATUS_LABELS[status]}
    </span>
  );
}

interface DiscoveredAgentCardProps {
  agent: DiscoveredAgent;
  busy?: boolean;
  onApprove: (agent: DiscoveredAgent) => void;
  onDismiss: (agent: DiscoveredAgent) => void;
}

export function DiscoveredAgentCard({ agent, busy, onApprove, onDismiss }: DiscoveredAgentCardProps) {
  const decidable = agent.status === 'shadow' || agent.status === 'pending';
  return (
    <div className="flex items-start justify-between gap-4 rounded-lg border border-gray-200 bg-white p-4">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-medium text-gray-900">{agent.name}</span>
          <StatusBadge status={agent.status} />
        </div>
        <div className="mt-1 text-xs text-gray-500">
          {agent.registry}
          {agent.runtime && <> · {agent.runtime}</>}
          {agent.region && <> · {agent.region}</>}
        </div>
        {agent.description && (
          <p className="mt-2 text-sm text-gray-600">{agent.description}</p>
        )}
        <div className="mt-2 text-xs text-gray-400">
          Last seen {new Date(agent.last_seen_at).toLocaleString()}
        </div>
      </div>
      {decidable && (
        <div className="flex shrink-0 gap-2">
          <button
            className="rounded border border-gray-300 px-3 py-1.5 text-xs text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            disabled={busy}
            onClick={() => onDismiss(agent)}
          >
            Dismiss
          </button>
          <button
            className="rounded bg-indigo-600 px-3 py-1.5 text-xs text-white hover:bg-indigo-500 disabled:opacity-50"
            disabled={busy}
            onClick={() => onApprove(agent)}
          >
            Approve
          </button>
        </div>
      )}
    </div>
  );
}
