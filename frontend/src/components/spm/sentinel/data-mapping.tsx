// ─────────────────────────────────────────────────────────────────────
// DataMapping — which Prompt Shields event types stream into the
// Sentinel custom table. Used both as an editable checklist inside the
// connect wizard's step 2, and read-only as a configuration summary
// once connected.
// ─────────────────────────────────────────────────────────────────────

import type { SentinelEventType } from '@/lib/aispm/sentinel';

export const EVENT_TYPE_META: Record<
  SentinelEventType,
  { label: string; description: string }
> = {
  Redacted: {
    label: 'Redacted',
    description: 'A sensitive span was replaced with a placeholder before send',
  },
  Anonymised: {
    label: 'Anonymised',
    description: 'Prompt was fully anonymised, reversible via the caller-held map',
  },
  Blocked: {
    label: 'Blocked',
    description: 'Prompt was blocked outright — nothing left the browser',
  },
  Coached: {
    label: 'Coached',
    description: 'User was shown an in-line policy nudge before sending',
  },
  BiasFlagged: {
    label: 'Bias flagged',
    description: 'Prompt was flagged for potential bias / protected-characteristic risk',
  },
};

interface DataMappingProps {
  selected: Set<SentinelEventType>;
  onToggle?: (type: SentinelEventType) => void;
  readOnly?: boolean;
}

export function DataMapping({ selected, onToggle, readOnly = false }: DataMappingProps) {
  return (
    <div className="space-y-2">
      {(Object.keys(EVENT_TYPE_META) as SentinelEventType[]).map((type) => {
        const meta = EVENT_TYPE_META[type];
        const checked = selected.has(type);
        return (
          <label
            key={type}
            className={`flex items-start gap-2.5 rounded-lg border px-3 py-2 text-xs transition-colors ${
              checked
                ? 'border-indigo-200 bg-indigo-50'
                : 'border-slate-200 bg-white'
            } ${readOnly ? '' : 'cursor-pointer hover:border-indigo-300'}`}
          >
            <input
              type="checkbox"
              checked={checked}
              disabled={readOnly}
              onChange={() => onToggle?.(type)}
              className="mt-0.5 h-3.5 w-3.5 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500 disabled:opacity-60"
            />
            <span className="min-w-0">
              <span className="block font-semibold text-slate-800">{meta.label}</span>
              <span className="block text-slate-500">{meta.description}</span>
            </span>
          </label>
        );
      })}
    </div>
  );
}
