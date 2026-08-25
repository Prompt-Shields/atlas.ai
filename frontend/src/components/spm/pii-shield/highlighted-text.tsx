// ─────────────────────────────────────────────────────────────────────
// HighlightedText — renders text with detected PII spans marked inline.
// Dumb/presentational: caller supplies the text and the (non-overlapping,
// start-sorted) spans from /pii/detect or /pii/anonymize.
// ─────────────────────────────────────────────────────────────────────

import type { ReactNode } from 'react';
import type { PIIClass, PIISpan } from '@/lib/pii';

interface HighlightedTextProps {
  text: string;
  spans: PIISpan[];
  className?: string;
}

const CLASS_STYLE: Record<PIIClass, string> = {
  EMAIL: 'bg-amber-200 text-amber-900',
  PHONE: 'bg-blue-200 text-blue-900',
  SSN: 'bg-red-200 text-red-900',
  CREDIT_CARD: 'bg-purple-200 text-purple-900',
  IP_ADDRESS: 'bg-cyan-200 text-cyan-900',
  API_KEY: 'bg-rose-200 text-rose-900',
};

export function HighlightedText({ text, spans, className }: HighlightedTextProps) {
  if (spans.length === 0) {
    return <span className={className}>{text}</span>;
  }

  const ordered = [...spans].sort((a, b) => a.start - b.start);
  const parts: ReactNode[] = [];
  let cursor = 0;

  ordered.forEach((span, i) => {
    if (span.start > cursor) {
      parts.push(text.slice(cursor, span.start));
    }
    parts.push(
      <mark
        key={`${span.start}-${span.end}-${i}`}
        title={`${span.pii_class} · ${Math.round(span.confidence * 100)}% confidence`}
        className={`rounded px-0.5 font-medium ${CLASS_STYLE[span.pii_class]}`}
      >
        {text.slice(span.start, span.end)}
      </mark>,
    );
    cursor = span.end;
  });

  if (cursor < text.length) {
    parts.push(text.slice(cursor));
  }

  return <span className={className}>{parts}</span>;
}
