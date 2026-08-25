'use client';

// /dashboard/integrations/sentinel — Microsoft Sentinel connector (issue #247).
//
// Ported from ai-spm-dashboard's app/integrations/sentinel/ (connect wizard +
// data mapping + event stream + side panel), reusing the existing
// integrations catalog. v1 stores the workspace/table + event-type mapping
// via POST /api/v1/integrations/sentinel/connect and renders a seeded event
// stream from GET /api/v1/integrations/sentinel/events — no live call to
// Azure Monitor Logs Ingestion API (see
// docs/feedbacks/integrations/microsoft-sentinel/spec.md for the real
// ingestion design, out of scope here).

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { RefreshCw, Settings2 } from 'lucide-react';
import { ConnectWizard } from '@/components/spm/sentinel/connect-wizard';
import { DataMapping, EVENT_TYPE_META } from '@/components/spm/sentinel/data-mapping';
import { EventStream } from '@/components/spm/sentinel/event-stream';
import { SidePanel } from '@/components/spm/sentinel/side-panel';
import {
  sentinelApi,
  type SentinelEvent,
  type SentinelEventStreamResponse,
  type SentinelEventType,
} from '@/lib/aispm/sentinel';

export default function SentinelIntegrationPage() {
  const [stream, setStream] = useState<SentinelEventStreamResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [reconfiguring, setReconfiguring] = useState(false);
  const [selected, setSelected] = useState<SentinelEvent | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await sentinelApi.events();
      setStream(res);
    } catch {
      setStream(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs text-slate-500">
            <Link href="/dashboard/integrations" className="hover:text-slate-700">
              Integrations
            </Link>{' '}
            / Microsoft Sentinel
          </p>
          <h1 className="mt-1 text-xl font-bold text-slate-900">Microsoft Sentinel</h1>
          <p className="mt-0.5 text-sm text-slate-500">
            Stream AI-relevant activity into your Sentinel workspace so the SOC can pivot from
            investigations into Prompt Shields activity.
          </p>
        </div>
        {stream?.connected && (
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => void load()}
              className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
            >
              <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
              Refresh
            </button>
            <button
              type="button"
              onClick={() => setReconfiguring((v) => !v)}
              className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
            >
              <Settings2 className="h-3.5 w-3.5" aria-hidden="true" />
              {reconfiguring ? 'Cancel' : 'Reconfigure'}
            </button>
          </div>
        )}
      </div>

      {loading && <p className="text-xs text-slate-400">Loading…</p>}

      {!loading && (!stream?.connected || reconfiguring) && (
        <ConnectWizard
          onConnected={() => {
            setReconfiguring(false);
            void load();
          }}
        />
      )}

      {!loading && stream?.connected && !reconfiguring && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
              Event stream
            </h2>
            <EventStream
              events={stream.events}
              selectedEventId={selected?.event_id ?? null}
              onSelect={setSelected}
            />
          </div>
          <div className="space-y-4">
            <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                Workspace
              </h2>
              <p className="mt-1 text-sm font-medium text-slate-900">{stream.workspace_name}</p>
              <p className="mt-0.5 font-mono text-[11px] text-slate-500">{stream.table_name}</p>
            </div>
            <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                Data mapping
              </h2>
              <p className="mt-1 text-[11px] text-slate-500">
                {stream.enabled_event_types.length} of {Object.keys(EVENT_TYPE_META).length} event
                types mapped
              </p>
              <div className="mt-3">
                <DataMapping
                  selected={new Set<SentinelEventType>(stream.enabled_event_types)}
                  readOnly
                />
              </div>
            </div>
          </div>
        </div>
      )}

      <SidePanel event={selected} tableName={stream?.table_name ?? null} onClose={() => setSelected(null)} />
    </div>
  );
}
