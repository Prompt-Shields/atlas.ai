// ─────────────────────────────────────────────────────────────────────
// AgentDrawer — per-agent detail panel with lifecycle/guardrail actions.
//
// Slide-over drawer (pattern shared with dashboard/model-risk's Drawer)
// showing the agent's identity, current health/lifecycle/guardrail
// state, and pause/quarantine/toggle-guardrail buttons. Actions are
// idempotent toggles on the backend, so labels flip to their "undo"
// phrasing once applied.
// ─────────────────────────────────────────────────────────────────────

import type { AgentControlAction, AgentControlState } from '@/lib/aispm/agent-control';
import { GuardrailBadge, HealthBadge, LifecycleBadge } from './badges';
import { PROVIDER_LABELS } from './fleet-table';

interface AgentDrawerProps {
  agent: AgentControlState | null;
  busy: boolean;
  onClose: () => void;
  onAction: (agent: AgentControlState, action: AgentControlAction) => void;
}

export function AgentDrawer({ agent, busy, onClose, onAction }: AgentDrawerProps) {
  if (!agent) return null;

  const isPaused = agent.lifecycle === 'paused';
  const isQuarantined = agent.lifecycle === 'quarantined';

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-gray-900/40"
      role="dialog"
      aria-modal="true"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="h-full w-full max-w-md overflow-y-auto bg-white p-6 shadow-xl">
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-base font-semibold text-gray-900">{agent.name}</h2>
            <p className="mt-0.5 text-xs text-gray-500">
              {PROVIDER_LABELS[agent.provider]} · {agent.registry}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
          >
            ✕
          </button>
        </div>

        <section className="mt-5 flex flex-wrap gap-2">
          <HealthBadge health={agent.health} />
          <LifecycleBadge lifecycle={agent.lifecycle} />
          <GuardrailBadge enabled={agent.guardrail_enabled} />
        </section>

        <section className="mt-5">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">Last seen</h3>
          <p className="mt-1 text-sm text-gray-700">{new Date(agent.last_seen_at).toLocaleString()}</p>
        </section>

        {agent.last_action && (
          <section className="mt-5">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">Last action</h3>
            <p className="mt-1 text-sm text-gray-700">
              {agent.last_action}
              {agent.last_action_at && ` · ${new Date(agent.last_action_at).toLocaleString()}`}
            </p>
          </section>
        )}

        <section className="mt-6 space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">Actions</h3>
          <button
            type="button"
            disabled={busy}
            onClick={() => onAction(agent, 'pause')}
            className="w-full rounded border border-gray-300 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          >
            {isPaused ? 'Resume agent' : 'Pause agent'}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => onAction(agent, 'quarantine')}
            className="w-full rounded border border-red-300 px-3 py-2 text-sm text-red-700 hover:bg-red-50 disabled:opacity-50"
          >
            {isQuarantined ? 'Release from quarantine' : 'Quarantine agent'}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => onAction(agent, 'toggle-guardrail')}
            className="w-full rounded bg-indigo-600 px-3 py-2 text-sm text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {agent.guardrail_enabled ? 'Disable guardrail' : 'Enable guardrail'}
          </button>
        </section>
      </div>
    </div>
  );
}
