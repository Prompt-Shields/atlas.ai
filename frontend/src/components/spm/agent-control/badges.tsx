// ─────────────────────────────────────────────────────────────────────
// Badges — health / lifecycle / guardrail pills for the agent control
// tower. Shared between the fleet table and the agent drawer.
// ─────────────────────────────────────────────────────────────────────

import type { AgentHealth, AgentLifecycle } from '@/lib/aispm/agent-control';

const HEALTH_LABELS: Record<AgentHealth, string> = {
  healthy: 'Healthy',
  degraded: 'Degraded',
  unhealthy: 'Unhealthy',
};

const HEALTH_TONES: Record<AgentHealth, string> = {
  healthy: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
  degraded: 'bg-amber-50 text-amber-700 ring-amber-600/20',
  unhealthy: 'bg-red-50 text-red-700 ring-red-600/20',
};

export function HealthBadge({ health }: { health: AgentHealth }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${HEALTH_TONES[health]}`}>
      {HEALTH_LABELS[health]}
    </span>
  );
}

const LIFECYCLE_LABELS: Record<AgentLifecycle, string> = {
  unmonitored: 'Unmonitored',
  provisioning: 'Provisioning',
  active: 'Active',
  paused: 'Paused',
  quarantined: 'Quarantined',
};

const LIFECYCLE_TONES: Record<AgentLifecycle, string> = {
  unmonitored: 'bg-gray-100 text-gray-600 ring-gray-500/20',
  provisioning: 'bg-sky-50 text-sky-700 ring-sky-600/20',
  active: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
  paused: 'bg-amber-50 text-amber-700 ring-amber-600/20',
  quarantined: 'bg-red-50 text-red-700 ring-red-600/20',
};

export function LifecycleBadge({ lifecycle }: { lifecycle: AgentLifecycle }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${LIFECYCLE_TONES[lifecycle]}`}>
      {LIFECYCLE_LABELS[lifecycle]}
    </span>
  );
}

export function GuardrailBadge({ enabled }: { enabled: boolean }) {
  const tone = enabled
    ? 'bg-emerald-50 text-emerald-700 ring-emerald-600/20'
    : 'bg-red-50 text-red-700 ring-red-600/20';
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${tone}`}>
      {enabled ? 'Guardrail on' : 'Guardrail off'}
    </span>
  );
}
