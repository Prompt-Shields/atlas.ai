// ─────────────────────────────────────────────────────────────────────
// CloudPane — per-provider group of discovered agents.
//
// Renders a provider header (label + agent count) followed by its
// agents' DiscoveredAgentCards. The page groups agents by provider
// before handing each group to a pane.
// ─────────────────────────────────────────────────────────────────────

import type { AgentCloudProvider, DiscoveredAgent } from '@/lib/aispm/agent-discovery';
import { DiscoveredAgentCard } from './discovered-agent-card';

export const PROVIDER_LABELS: Record<AgentCloudProvider, string> = {
  aws_bedrock_agentcore: 'AWS Bedrock AgentCore',
  azure_ai_foundry: 'Azure AI Foundry',
  gcp_knowledge_catalog: 'GCP Knowledge Catalog',
};

interface CloudPaneProps {
  provider: AgentCloudProvider;
  agents: DiscoveredAgent[];
  busyId: string | null;
  onApprove: (agent: DiscoveredAgent) => void;
  onDismiss: (agent: DiscoveredAgent) => void;
}

export function CloudPane({ provider, agents, busyId, onApprove, onDismiss }: CloudPaneProps) {
  if (agents.length === 0) return null;
  return (
    <section>
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="text-lg font-semibold text-gray-900">{PROVIDER_LABELS[provider]}</h2>
        <span className="text-sm text-gray-500">{agents.length} agent{agents.length === 1 ? '' : 's'}</span>
      </div>
      <div className="space-y-3">
        {agents.map((agent) => (
          <DiscoveredAgentCard
            key={agent.id}
            agent={agent}
            busy={busyId === agent.id}
            onApprove={onApprove}
            onDismiss={onDismiss}
          />
        ))}
      </div>
    </section>
  );
}
