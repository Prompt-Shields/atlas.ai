// ─────────────────────────────────────────────────────────────────────
// PolicyPane — two-column Strict | Loose (Guideline) instance tables.
// Strict rows carry a Demote action (always permitted — the safety
// valve, no approval required); Loose rows are read-only here (their
// actions live in the promotion queue).
// ─────────────────────────────────────────────────────────────────────

import type { PolicyInstance, PolicySeverity, PolicyTemplate } from '@/lib/aispm/pep';

const SEVERITY_CLASSES: Record<PolicySeverity, string> = {
  low: 'bg-gray-100 text-gray-700',
  medium: 'bg-amber-100 text-amber-700',
  high: 'bg-orange-100 text-orange-700',
  critical: 'bg-red-100 text-red-700',
};

function SeverityBadge({ severity }: { severity: PolicySeverity }) {
  return (
    <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${SEVERITY_CLASSES[severity]}`}>
      {severity}
    </span>
  );
}

interface PolicyRowProps {
  instance: PolicyInstance;
  template: PolicyTemplate | undefined;
  busy: boolean;
  onDemote?: (instance: PolicyInstance) => void;
}

function PolicyRow({ instance, template, busy, onDemote }: PolicyRowProps) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-gray-100 py-2.5 last:border-0">
      <div className="min-w-0">
        <p className="truncate text-sm font-medium text-gray-900">{instance.name}</p>
        <p className="text-xs text-gray-500">
          {template?.category ?? '—'} · {instance.enforcement_mode}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <SeverityBadge severity={instance.severity} />
        {onDemote && (
          <button
            type="button"
            disabled={busy}
            onClick={() => onDemote(instance)}
            className="rounded-md border border-gray-300 bg-white px-2.5 py-1 text-[11px] font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          >
            Demote
          </button>
        )}
      </div>
    </div>
  );
}

interface PolicyPaneProps {
  strict: PolicyInstance[];
  loose: PolicyInstance[];
  templatesById: Map<string, PolicyTemplate>;
  busyId: string | null;
  onDemote: (instance: PolicyInstance) => void;
}

export function PolicyPane({ strict, loose, templatesById, busyId, onDemote }: PolicyPaneProps) {
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
        <h2 className="text-sm font-semibold text-gray-900">Strict ({strict.length})</h2>
        <p className="mt-0.5 text-xs text-gray-500">Real-time enforcement — block or redact.</p>
        <div className="mt-3">
          {strict.length === 0 && <p className="text-xs text-gray-500">No policies are Strict yet.</p>}
          {strict.map((i) => (
            <PolicyRow
              key={i.id}
              instance={i}
              template={templatesById.get(i.template_id)}
              busy={busyId === i.id}
              onDemote={onDemote}
            />
          ))}
        </div>
      </div>

      <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
        <h2 className="text-sm font-semibold text-gray-900">Loose ({loose.length})</h2>
        <p className="mt-0.5 text-xs text-gray-500">Guideline — advisory log or flag, observing only.</p>
        <div className="mt-3">
          {loose.length === 0 && <p className="text-xs text-gray-500">No policies are in Guideline mode.</p>}
          {loose.map((i) => (
            <PolicyRow key={i.id} instance={i} template={templatesById.get(i.template_id)} busy={false} />
          ))}
        </div>
      </div>
    </div>
  );
}
