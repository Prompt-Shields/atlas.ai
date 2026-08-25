// ─────────────────────────────────────────────────────────────────────
// MappingPanel — shows the placeholder → original-value reversal map
// returned by /pii/anonymize. Originals are masked by default (this is a
// security demo — the whole point is that the raw values shouldn't sit
// in plaintext on screen) with a reveal toggle per row.
// ─────────────────────────────────────────────────────────────────────
'use client';

import { useState } from 'react';
import { Eye, EyeOff } from 'lucide-react';

interface MappingPanelProps {
  placeholderMap: Record<string, string>;
}

function maskValue(value: string): string {
  if (value.length <= 4) return '•'.repeat(value.length);
  return `${value.slice(0, 2)}${'•'.repeat(Math.min(value.length - 4, 8))}${value.slice(-2)}`;
}

export function MappingPanel({ placeholderMap }: MappingPanelProps) {
  const [revealed, setRevealed] = useState<Set<string>>(new Set());
  const entries = Object.entries(placeholderMap);

  const toggle = (placeholder: string) => {
    setRevealed(prev => {
      const next = new Set(prev);
      if (next.has(placeholder)) next.delete(placeholder);
      else next.add(placeholder);
      return next;
    });
  };

  return (
    <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
      <div className="text-sm font-semibold text-slate-800 mb-1">Placeholder mapping</div>
      <p className="text-xs text-slate-500 mb-3">
        Reversible only with this map — nothing is persisted server-side.
      </p>
      {entries.length === 0 ? (
        <div className="text-xs text-slate-400 italic">No PII anonymized yet.</div>
      ) : (
        <div className="space-y-1.5">
          {entries.map(([placeholder, original]) => {
            const isRevealed = revealed.has(placeholder);
            return (
              <div
                key={placeholder}
                className="flex items-center justify-between gap-2 text-xs bg-slate-50 border border-slate-100 rounded-lg px-2.5 py-1.5"
              >
                <code className="font-mono text-slate-700 font-medium">{placeholder}</code>
                <div className="flex items-center gap-1.5 min-w-0">
                  <code className="font-mono text-slate-500 truncate">
                    {isRevealed ? original : maskValue(original)}
                  </code>
                  <button
                    type="button"
                    onClick={() => toggle(placeholder)}
                    className="text-slate-400 hover:text-slate-600 flex-shrink-0"
                    aria-label={isRevealed ? `Hide ${placeholder} value` : `Reveal ${placeholder} value`}
                  >
                    {isRevealed ? <EyeOff size={13} /> : <Eye size={13} />}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
