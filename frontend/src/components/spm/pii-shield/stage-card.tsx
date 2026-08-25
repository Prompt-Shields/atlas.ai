// ─────────────────────────────────────────────────────────────────────
// StageCard — the 4-step anonymize → send → re-identify pipeline status:
// Detect, Anonymize, Send, Re-identify. Purely presentational; the caller
// drives `activeStage`/`completedStages` from its own request state.
// ─────────────────────────────────────────────────────────────────────

import { Check } from 'lucide-react';

export type PiiStage = 'detect' | 'anonymize' | 'send' | 'reidentify';

const STAGES: { key: PiiStage; label: string; description: string }[] = [
  { key: 'detect', label: 'Detect', description: 'Scan the prompt for PII spans' },
  { key: 'anonymize', label: 'Anonymize', description: 'Redact spans, keep the reversal map' },
  { key: 'send', label: 'Send', description: 'Only the redacted text leaves the browser' },
  { key: 'reidentify', label: 'Re-identify', description: 'Restore originals in the reply' },
];

interface StageCardProps {
  activeStage: PiiStage | null;
  completedStages: Set<PiiStage>;
}

export function StageCard({ activeStage, completedStages }: StageCardProps) {
  return (
    <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
      <div className="text-sm font-semibold text-slate-800 mb-3">Pipeline</div>
      <div className="space-y-3">
        {STAGES.map((stage, i) => {
          const done = completedStages.has(stage.key);
          const active = activeStage === stage.key;
          return (
            <div key={stage.key} className="flex items-start gap-2.5">
              <div
                className={`flex-shrink-0 w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold mt-0.5 ${
                  done
                    ? 'bg-emerald-500 text-white'
                    : active
                      ? 'bg-indigo-100 text-indigo-700 ring-2 ring-indigo-300'
                      : 'bg-slate-100 text-slate-400'
                }`}
              >
                {done ? <Check size={12} /> : i + 1}
              </div>
              <div className="min-w-0">
                <div
                  className={`text-xs font-semibold ${
                    done || active ? 'text-slate-800' : 'text-slate-400'
                  }`}
                >
                  {stage.label}
                </div>
                <div className="text-[11px] text-slate-500">{stage.description}</div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
