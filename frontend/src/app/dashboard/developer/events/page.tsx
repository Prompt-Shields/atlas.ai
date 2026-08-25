'use client';

import { useEffect, useState } from 'react';
import {
  ALL_SEVERITIES,
  developerApi,
  type DeveloperEvent,
  type EventDefinition,
  type EventSeverity,
} from '@/lib/developer';

export default function DeveloperEventsPage() {
  const [events, setEvents] = useState<DeveloperEvent[]>([]);
  const [definitions, setDefinitions] = useState<EventDefinition[]>([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<DeveloperEvent | null>(null);

  // Filters
  const [filterType, setFilterType] = useState<string>('');
  const [filterSeverity, setFilterSeverity] = useState<EventSeverity | ''>('');
  const [filterSource, setFilterSource] = useState<string>('');
  const [filterSession, setFilterSession] = useState<string>('');
  const [filterCorrelation, setFilterCorrelation] = useState<string>('');

  const fetchPage = async (resetCursor: boolean) => {
    setLoading(true);
    setError(null);
    try {
      const res = await developerApi.listEvents({
        event_type: filterType || undefined,
        severity: filterSeverity || undefined,
        source: filterSource || undefined,
        session_id: filterSession || undefined,
        correlation_id: filterCorrelation || undefined,
        after: resetCursor ? undefined : cursor || undefined,
        limit: 50,
      });
      setEvents(resetCursor ? res.events : [...events, ...res.events]);
      setTotal(res.total);
      setHasMore(res.has_more);
      setCursor(res.next_cursor);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void developerApi
      .listEventTypes(true)
      .then((r) => setDefinitions(r.definitions))
      .catch(() => {});
  }, []);

  useEffect(() => {
    void fetchPage(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterType, filterSeverity, filterSource, filterSession, filterCorrelation]);

  return (
    <div className="p-8 max-w-7xl">
      <h1 className="text-3xl font-semibold mb-2">Events</h1>
      <p className="text-gray-600 mb-6">
        Review events sent by your developers via the Developer API.
      </p>

      {/* Filters */}
      <div className="grid grid-cols-1 md:grid-cols-5 gap-3 mb-6 p-3 bg-gray-50 rounded">
        <div>
          <label className="block text-xs font-medium mb-1">Event type</label>
          <select
            value={filterType}
            onChange={(e) => setFilterType(e.target.value)}
            className="w-full px-2 py-1 border rounded text-sm"
          >
            <option value="">All</option>
            {definitions.map((d) => (
              <option key={d.id} value={d.name}>
                {d.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium mb-1">Severity</label>
          <select
            value={filterSeverity}
            onChange={(e) =>
              setFilterSeverity(e.target.value as EventSeverity | '')
            }
            className="w-full px-2 py-1 border rounded text-sm"
          >
            <option value="">All</option>
            {ALL_SEVERITIES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium mb-1">Source</label>
          <input
            value={filterSource}
            onChange={(e) => setFilterSource(e.target.value)}
            placeholder="any"
            className="w-full px-2 py-1 border rounded text-sm"
          />
        </div>
        <div>
          <label className="block text-xs font-medium mb-1">Session ID</label>
          <input
            value={filterSession}
            onChange={(e) => setFilterSession(e.target.value)}
            placeholder="any"
            className="w-full px-2 py-1 border rounded text-sm"
          />
        </div>
        <div>
          <label className="block text-xs font-medium mb-1">
            Correlation ID
          </label>
          <input
            value={filterCorrelation}
            onChange={(e) => setFilterCorrelation(e.target.value)}
            placeholder="any"
            className="w-full px-2 py-1 border rounded text-sm"
          />
        </div>
      </div>

      <div className="flex justify-between items-center mb-3">
        <p className="text-sm text-gray-600">
          {total.toLocaleString()} events match
        </p>
        <button
          onClick={() => fetchPage(true)}
          className="text-sm text-indigo-600 hover:underline"
        >
          Refresh
        </button>
      </div>

      {error && <p className="text-red-600 mb-3">{error}</p>}

      <div className="border rounded overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left">
            <tr>
              <th className="py-2 px-3">Time</th>
              <th className="py-2 px-3">Type</th>
              <th className="py-2 px-3">Severity</th>
              <th className="py-2 px-3">Source</th>
              <th className="py-2 px-3">Session</th>
              <th className="py-2 px-3">Correlation</th>
              <th className="py-2 px-3">Count</th>
              <th className="py-2 px-3"></th>
            </tr>
          </thead>
          <tbody>
            {events.length === 0 && !loading && (
              <tr>
                <td colSpan={8} className="py-6 text-center text-gray-500 italic">
                  No events match these filters.
                </td>
              </tr>
            )}
            {events.map((e) => (
              <tr
                key={e.id}
                className="border-t hover:bg-gray-50 cursor-pointer"
                onClick={() => setSelected(e)}
              >
                <td className="py-2 px-3 whitespace-nowrap text-xs text-gray-600">
                  {new Date(e.occurred_at).toLocaleString()}
                </td>
                <td className="py-2 px-3 font-mono text-xs">{e.event_type}</td>
                <td className="py-2 px-3">
                  <SeverityBadge severity={e.severity} />
                </td>
                <td className="py-2 px-3 text-xs">
                  {e.source || <span className="text-gray-400">—</span>}
                </td>
                <td className="py-2 px-3 font-mono text-xs">
                  {e.session_id ? truncate(e.session_id, 12) : '—'}
                </td>
                <td className="py-2 px-3 font-mono text-xs">
                  {e.correlation_id ? truncate(e.correlation_id, 12) : '—'}
                </td>
                <td className="py-2 px-3 text-xs">
                  {e.occurrences > 1 ? `×${e.occurrences}` : ''}
                </td>
                <td className="py-2 px-3 text-right">
                  <button
                    onClick={(ev) => {
                      ev.stopPropagation();
                      setSelected(e);
                    }}
                    className="text-xs text-indigo-600 hover:underline"
                  >
                    Inspect
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {hasMore && (
        <div className="mt-4 text-center">
          <button
            onClick={() => fetchPage(false)}
            disabled={loading}
            className="px-4 py-1.5 border rounded text-sm hover:bg-gray-50 disabled:opacity-50"
          >
            {loading ? 'Loading…' : 'Load more'}
          </button>
        </div>
      )}

      {/* Detail drawer */}
      {selected && (
        <EventDetailDrawer
          event={selected}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}

function EventDetailDrawer({
  event,
  onClose,
}: {
  event: DeveloperEvent;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 bg-black/40 z-40" onClick={onClose}>
      <div
        className="absolute right-0 top-0 h-full w-full max-w-xl bg-white shadow-2xl overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="p-6 border-b">
          <div className="flex items-start justify-between">
            <div>
              <h2 className="text-xl font-semibold font-mono">
                {event.event_type}
              </h2>
              <p className="text-xs text-gray-500 mt-1">
                {new Date(event.occurred_at).toLocaleString()}
              </p>
            </div>
            <button
              onClick={onClose}
              className="text-2xl text-gray-400 hover:text-gray-700"
            >
              ×
            </button>
          </div>
          <div className="flex items-center gap-3 mt-3">
            <SeverityBadge severity={event.severity} />
            {event.occurrences > 1 && (
              <span className="text-xs text-gray-500">
                {event.occurrences} occurrences
              </span>
            )}
          </div>
        </div>
        <div className="p-6 space-y-4">
          <Field label="Event ID" value={event.id} mono />
          <Field label="Source" value={event.source} mono />
          <Field label="Session ID" value={event.session_id} mono />
          <Field label="Correlation ID" value={event.correlation_id} mono />
          <Field label="User external ID" value={event.user_external_id} mono />
          <Field
            label="Created at"
            value={new Date(event.created_at).toLocaleString()}
          />

          <div>
            <div className="text-xs font-medium text-gray-500 uppercase mb-1">
              Payload
            </div>
            <pre className="bg-gray-50 border rounded p-3 text-xs font-mono overflow-x-auto">
              {JSON.stringify(event.payload, null, 2)}
            </pre>
          </div>
        </div>
      </div>
    </div>
  );
}

function Field({
  label,
  value,
  mono,
}: {
  label: string;
  value: string | null | undefined;
  mono?: boolean;
}) {
  return (
    <div>
      <div className="text-xs font-medium text-gray-500 uppercase">{label}</div>
      <div
        className={`text-sm mt-0.5 ${mono ? 'font-mono' : ''} ${
          value ? '' : 'text-gray-400'
        }`}
      >
        {value || '—'}
      </div>
    </div>
  );
}

function SeverityBadge({ severity }: { severity: EventSeverity }) {
  const color = {
    debug: 'bg-gray-100 text-gray-700',
    info: 'bg-blue-100 text-blue-800',
    warning: 'bg-amber-100 text-amber-800',
    error: 'bg-red-100 text-red-800',
    critical: 'bg-red-200 text-red-900 font-bold',
  }[severity];
  return (
    <span className={`text-xs px-2 py-0.5 rounded ${color}`}>{severity}</span>
  );
}

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n)}…` : s;
}
