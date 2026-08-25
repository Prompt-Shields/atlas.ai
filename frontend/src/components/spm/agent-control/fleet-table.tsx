// ─────────────────────────────────────────────────────────────────────
// FleetTable — governed-agent list for the control panel.
//
// One row per agent with identity, provider, health/lifecycle/guardrail
// badges, and last-seen. Clicking a row opens the detail drawer; the
// page owns which agent (if any) is open.
// ─────────────────────────────────────────────────────────────────────

import type { AgentCloudProvider, AgentControlState } from '@/lib/aispm/agent-control';
import { GuardrailBadge, HealthBadge, LifecycleBadge } from './badges';

const PROVIDER_LABELS: Record<AgentCloudProvider, string> = {
  aws_bedrock_agentcore: 'AWS Bedrock AgentCore',
  azure_ai_foundry: 'Azure AI Foundry',
  gcp_knowledge_catalog: 'GCP Knowledge Catalog',
};

interface FleetTableProps {
  agents: AgentControlState[];
  onSelect: (agent: AgentControlState) => void;
}

export function FleetTable({ agents, onSelect }: FleetTableProps) {
  if (agents.length === 0) {
    return <div className="py-12 text-center text-gray-500">No governed agents yet.</div>;
  }

  return (
    <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
      <table className="min-w-full divide-y divide-gray-200">
        <thead className="bg-gray-50">
          <tr>
            <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wide text-gray-500">Agent</th>
            <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wide text-gray-500">Provider</th>
            <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wide text-gray-500">Health</th>
            <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wide text-gray-500">Lifecycle</th>
            <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wide text-gray-500">Guardrail</th>
            <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wide text-gray-500">Last seen</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {agents.map((agent) => (
            <tr
              key={agent.id}
              className="cursor-pointer hover:bg-gray-50"
              onClick={() => onSelect(agent)}
            >
              <td className="px-4 py-3">
                <div className="font-medium text-gray-900">{agent.name}</div>
                <div className="text-xs text-gray-500">{agent.registry}</div>
              </td>
              <td className="px-4 py-3 text-sm text-gray-600">{PROVIDER_LABELS[agent.provider]}</td>
              <td className="px-4 py-3"><HealthBadge health={agent.health} /></td>
              <td className="px-4 py-3"><LifecycleBadge lifecycle={agent.lifecycle} /></td>
              <td className="px-4 py-3"><GuardrailBadge enabled={agent.guardrail_enabled} /></td>
              <td className="px-4 py-3 text-xs text-gray-400">
                {new Date(agent.last_seen_at).toLocaleString()}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export { PROVIDER_LABELS };
