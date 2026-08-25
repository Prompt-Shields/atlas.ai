// ─────────────────────────────────────────────────────────────────────
// EventStream — table of AI-relevant events mapped into the Sentinel
// custom table. Seeded (issue #247, v1 — no live Azure Monitor call).
// Rows are clickable; the caller renders SidePanel for the selection.
// ─────────────────────────────────────────────────────────────────────

import { ShieldAlert } from 'lucide-react';
import type { SentinelEvent, SentinelSeverity } from '@/lib/aispm/sentinel';

const SEVERITY_CLASSES: Record<SentinelSeverity, string> = {
  Low: 'bg-slate-100 text-slate-600',
  Medium: 'bg-amber-50 text-amber-700 ring-1 ring-amber-200',
  High: 'bg-red-50 text-red-700 ring-1 ring-red-200',
};

const EVENT_TYPE_CLASSES: Record<string, string> = {
  Redacted: 'bg-blue-50 text-blue-700',
  Anonymised: 'bg-purple-50 text-purple-700',
  Blocked: 'bg-red-50 text-red-700',
  Coached: 'bg-emerald-50 text-emerald-700',
  BiasFlagged: 'bg-amber-50 text-amber-700',
};

interface EventStreamProps {
  events: SentinelEvent[];
  selectedEventId: string | null;
  onSelect: (event: SentinelEvent) => void;
}

export function EventStream({ events, selectedEventId, onSelect }: EventStreamProps) {
  if (events.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-slate-200 bg-white px-4 py-8 text-center text-xs text-slate-500">
        No event types are mapped — reconfigure the data mapping to see events here.
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-slate-100 text-xs">
          <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-3 py-2 text-left font-medium">Time</th>
              <th className="px-3 py-2 text-left font-medium">User</th>
              <th className="px-3 py-2 text-left font-medium">AI tool</th>
              <th className="px-3 py-2 text-left font-medium">Event type</th>
              <th className="px-3 py-2 text-left font-medium">Severity</th>
              <th className="px-3 py-2 text-left font-medium">Detail</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {events.map((event) => (
              <tr
                key={event.event_id}
                onClick={() => onSelect(event)}
                className={`cursor-pointer transition-colors hover:bg-slate-50 ${
                  selectedEventId === event.event_id ? 'bg-indigo-50/60' : ''
                }`}
              >
                <td className="whitespace-nowrap px-3 py-2 text-slate-500">
                  {new Date(event.time_generated).toLocaleTimeString(undefined, {
                    hour: '2-digit',
                    minute: '2-digit',
                  })}
                </td>
                <td className="whitespace-nowrap px-3 py-2 text-slate-700">{event.user}</td>
                <td className="whitespace-nowrap px-3 py-2 text-slate-700">
                  <span className="inline-flex items-center gap-1">
                    {event.ai_tool}
                    {event.is_shadow_ai && (
                      <ShieldAlert
                        className="h-3 w-3 text-amber-500"
                        aria-label="Shadow AI"
                      />
                    )}
                  </span>
                </td>
                <td className="whitespace-nowrap px-3 py-2">
                  <span
                    className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${
                      EVENT_TYPE_CLASSES[event.event_type] ?? 'bg-slate-100 text-slate-600'
                    }`}
                  >
                    {event.event_type}
                  </span>
                </td>
                <td className="whitespace-nowrap px-3 py-2">
                  <span
                    className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${SEVERITY_CLASSES[event.severity]}`}
                  >
                    {event.severity}
                  </span>
                </td>
                <td className="max-w-xs truncate px-3 py-2 text-slate-600">{event.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
