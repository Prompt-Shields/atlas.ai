// ─────────────────────────────────────────────────────────────────────
// SidePanel — full detail view for a selected Sentinel event, shaped
// like the PromptShieldsActivity_CL custom table columns (see
// docs/integrations/microsoft-sentinel/data-schema.md).
// Never shows a prompt body — only the structured Detail description
// and the PromptHash.
// ─────────────────────────────────────────────────────────────────────

import { X } from 'lucide-react';
import type { SentinelEvent } from '@/lib/aispm/sentinel';

interface SidePanelProps {
  event: SentinelEvent | null;
  tableName: string | null;
  onClose: () => void;
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">
        {label}
      </div>
      <div className="mt-0.5 text-xs text-slate-800">{value}</div>
    </div>
  );
}

export function SidePanel({ event, tableName, onClose }: SidePanelProps) {
  if (!event) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-slate-900/40"
      role="dialog"
      aria-modal="true"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="h-full w-full max-w-sm overflow-y-auto bg-white p-5 shadow-xl">
        <div className="flex items-start justify-between">
          <div>
            <p className="text-[10px] font-medium uppercase tracking-wide text-slate-400">
              {tableName ?? 'PromptShieldsActivity_CL'}
            </p>
            <h2 className="mt-0.5 text-sm font-semibold text-slate-900">{event.event_id}</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        <div className="mt-5 grid grid-cols-2 gap-4">
          <Field
            label="Time generated"
            value={new Date(event.time_generated).toLocaleString()}
          />
          <Field label="Event type" value={event.event_type} />
          <Field label="Severity" value={event.severity} />
          <Field label="Shadow AI" value={event.is_shadow_ai ? 'Yes' : 'No'} />
          <Field label="User" value={event.user} />
          <Field label="AI tool" value={event.ai_tool} />
        </div>

        <div className="mt-4">
          <Field label="Sensitive type" value={event.sensitive_type ?? '—'} />
        </div>

        <div className="mt-4">
          <Field label="Detail" value={event.detail} />
        </div>

        <div className="mt-4">
          <Field
            label="Prompt hash (SHA-256, truncated)"
            value={<code className="font-mono text-[11px] text-slate-600">{event.prompt_hash}</code>}
          />
        </div>

        <p className="mt-6 text-[11px] text-slate-400">
          The prompt body is never sent to Sentinel — only this structured detail and a hash for
          correlation.
        </p>
      </div>
    </div>
  );
}
