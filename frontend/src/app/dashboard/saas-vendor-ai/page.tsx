'use client';

// ─────────────────────────────────────────────────────────────────────
// SaaS Vendor AI — /dashboard/saas-vendor-ai  (SP-1, issue #195)
//
// Third-party / supply-chain AI risk. Wired to /api/v1/saas-vendor-ai via
// lib/vendors.ts: KPI strip, search + category filter, expandable rows,
// CSV export, owner column + provenance chip, and Request / Import
// assessment modals.
// ─────────────────────────────────────────────────────────────────────

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  vendorsApi,
  type AssessmentImportSource,
  type SaaSVendor,
  type VendorCategory,
  type VendorKpi,
} from '@/lib/vendors';

const CATEGORY_LABELS: Record<VendorCategory, string> = {
  llm_provider: 'LLM Provider',
  productivity_suite: 'Productivity Suite',
  developer_tool: 'Developer Tool',
  crm_support: 'CRM / Support',
  data_analytics: 'Data & Analytics',
};

const IMPORT_SOURCES: { value: AssessmentImportSource; label: string }[] = [
  { value: 'peer_shared', label: 'Peer-shared' },
  { value: 'trust_center', label: 'Trust Center' },
  { value: 'upload', label: 'Upload' },
];

function riskTone(score: number): string {
  if (score >= 70) return 'bg-red-50 text-red-700 ring-red-600/20';
  if (score >= 40) return 'bg-amber-50 text-amber-700 ring-amber-600/20';
  return 'bg-green-50 text-green-700 ring-green-600/20';
}

// ─── KPI strip ───────────────────────────────────────────────────────

function KpiStrip({ kpi }: { kpi: VendorKpi | null }) {
  const cards = [
    { label: 'Vendors', value: kpi?.total ?? '—' },
    { label: 'High risk', value: kpi?.high_risk ?? '—', tone: 'text-red-600' },
    { label: 'Trains on data', value: kpi?.trains_on_data ?? '—', tone: 'text-amber-600' },
    { label: 'No DPA', value: kpi?.no_dpa ?? '—', tone: 'text-amber-600' },
  ];
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
      {cards.map((c) => (
        <div key={c.label} className="p-4 border border-gray-200 rounded-lg bg-white">
          <div className="text-sm text-gray-500">{c.label}</div>
          <div className={`text-2xl font-semibold ${c.tone ?? 'text-gray-900'}`}>{c.value}</div>
        </div>
      ))}
    </div>
  );
}

// ─── Provenance chip ─────────────────────────────────────────────────

function ProvenanceChip({ vendor }: { vendor: SaaSVendor }) {
  if (vendor.has_assessment) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-700 ring-1 ring-inset ring-indigo-600/20">
        ✓ Assessed
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-gray-50 px-2 py-0.5 text-xs font-medium text-gray-600 ring-1 ring-inset ring-gray-500/20">
      {vendor.source || 'unverified'}
    </span>
  );
}

// ─── Expanded detail ─────────────────────────────────────────────────

function DetailList({ label, items }: { label: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div>
      <div className="text-xs font-semibold uppercase tracking-wide text-gray-400">{label}</div>
      <div className="mt-1 flex flex-wrap gap-1">
        {items.map((it) => (
          <span key={it} className="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-700">
            {it}
          </span>
        ))}
      </div>
    </div>
  );
}

function VendorDetail({ vendor }: { vendor: SaaSVendor }) {
  return (
    <div className="grid grid-cols-1 gap-4 bg-gray-50 p-4 md:grid-cols-2">
      <DetailList label="Models" items={vendor.models} />
      <DetailList label="Sub-processors" items={vendor.sub_processors} />
      <DetailList label="Certifications" items={vendor.certifications} />
      <div>
        <div className="text-xs font-semibold uppercase tracking-wide text-gray-400">Data flows</div>
        {vendor.data_flows.length === 0 ? (
          <div className="mt-1 text-xs text-gray-500">None recorded</div>
        ) : (
          <ul className="mt-1 space-y-1">
            {vendor.data_flows.map((f, i) => (
              <li key={i} className="text-xs text-gray-700">
                {f.description}{' '}
                <span className="text-gray-400">({f.classification})</span>
                {f.trains_on_data && (
                  <span className="ml-1 text-amber-600">· trains on data</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="md:col-span-2 flex flex-wrap gap-4 text-xs text-gray-600">
        <span>DPA: {vendor.dpa ? 'yes' : 'no'}</span>
        <span>Training opt-out: {vendor.training_opt_out ? 'yes' : 'no'}</span>
        <span>Contract: {vendor.contract_status}</span>
        {vendor.last_reviewed_at && <span>Last reviewed: {vendor.last_reviewed_at}</span>}
      </div>
    </div>
  );
}

// ─── Modals ──────────────────────────────────────────────────────────

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <h3 className="mb-4 text-lg font-semibold">{title}</h3>
        {children}
      </div>
    </div>
  );
}

function RequestModal({ vendor, onClose, onDone }: { vendor: SaaSVendor; onClose: () => void; onDone: () => void }) {
  const [questionnaire, setQuestionnaire] = useState('SIG-Lite');
  const [notes, setNotes] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      await vendorsApi.requestAssessment(vendor.id, { questionnaire, notes });
      onDone();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed');
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={`Request assessment — ${vendor.name}`} onClose={onClose}>
      <label className="mb-1 block text-sm text-gray-600">Questionnaire</label>
      <input className="mb-3 w-full rounded border border-gray-300 px-3 py-2 text-sm" value={questionnaire} onChange={(e) => setQuestionnaire(e.target.value)} />
      <label className="mb-1 block text-sm text-gray-600">Notes</label>
      <textarea className="mb-3 w-full rounded border border-gray-300 px-3 py-2 text-sm" value={notes} onChange={(e) => setNotes(e.target.value)} />
      {err && <p className="mb-2 text-sm text-red-600">{err}</p>}
      <div className="flex justify-end gap-2">
        <button className="rounded px-3 py-2 text-sm text-gray-600" onClick={onClose}>Cancel</button>
        <button className="rounded bg-indigo-600 px-3 py-2 text-sm text-white disabled:opacity-50" disabled={busy} onClick={submit}>
          {busy ? 'Sending…' : 'Send request'}
        </button>
      </div>
    </Modal>
  );
}

function ImportModal({ vendor, onClose, onDone }: { vendor: SaaSVendor; onClose: () => void; onDone: () => void }) {
  const [source, setSource] = useState<AssessmentImportSource>('trust_center');
  const [reference, setReference] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      await vendorsApi.importAssessment(vendor.id, { source, reference });
      onDone();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed');
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={`Import assessment — ${vendor.name}`} onClose={onClose}>
      <label className="mb-1 block text-sm text-gray-600">Source</label>
      <select className="mb-3 w-full rounded border border-gray-300 px-3 py-2 text-sm" value={source} onChange={(e) => setSource(e.target.value as AssessmentImportSource)}>
        {IMPORT_SOURCES.map((s) => (
          <option key={s.value} value={s.value}>{s.label}</option>
        ))}
      </select>
      <label className="mb-1 block text-sm text-gray-600">Reference (URL or id)</label>
      <input className="mb-3 w-full rounded border border-gray-300 px-3 py-2 text-sm" value={reference} onChange={(e) => setReference(e.target.value)} />
      {err && <p className="mb-2 text-sm text-red-600">{err}</p>}
      <div className="flex justify-end gap-2">
        <button className="rounded px-3 py-2 text-sm text-gray-600" onClick={onClose}>Cancel</button>
        <button className="rounded bg-indigo-600 px-3 py-2 text-sm text-white disabled:opacity-50" disabled={busy} onClick={submit}>
          {busy ? 'Importing…' : 'Import'}
        </button>
      </div>
    </Modal>
  );
}

// ─── Page ────────────────────────────────────────────────────────────

export default function SaaSVendorAiPage() {
  const [vendors, setVendors] = useState<SaaSVendor[]>([]);
  const [kpi, setKpi] = useState<VendorKpi | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState<VendorCategory | 'all'>('all');
  const [expanded, setExpanded] = useState<string | null>(null);
  const [requesting, setRequesting] = useState<SaaSVendor | null>(null);
  const [importing, setImporting] = useState<SaaSVendor | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [v, k] = await Promise.all([vendorsApi.list(), vendorsApi.kpi()]);
      setVendors(v);
      setKpi(k);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load vendors');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return vendors.filter((v) => {
      if (category !== 'all' && v.category !== category) return false;
      if (!q) return true;
      return (
        v.name.toLowerCase().includes(q) ||
        v.ai_feature.toLowerCase().includes(q) ||
        v.department.toLowerCase().includes(q)
      );
    });
  }, [vendors, query, category]);

  const downloadCsv = async () => {
    try {
      const body = await vendorsApi.exportCsv();
      const url = URL.createObjectURL(new Blob([body], { type: 'text/csv' }));
      const a = document.createElement('a');
      a.href = url;
      a.download = 'saas-vendor-ai.csv';
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'CSV export failed');
    }
  };

  return (
    <div className="p-8">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold">SaaS Vendor AI</h1>
          <p className="text-gray-600">Third-party &amp; supply-chain AI risk.</p>
        </div>
        <button className="rounded border border-gray-300 px-3 py-2 text-sm hover:bg-gray-50" onClick={downloadCsv}>
          Export CSV
        </button>
      </div>

      <div className="mb-6">
        <KpiStrip kpi={kpi} />
      </div>

      <div className="mb-4 flex flex-wrap gap-3">
        <input
          className="w-64 rounded border border-gray-300 px-3 py-2 text-sm"
          placeholder="Search vendors…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <select
          className="rounded border border-gray-300 px-3 py-2 text-sm"
          value={category}
          onChange={(e) => setCategory(e.target.value as VendorCategory | 'all')}
        >
          <option value="all">All categories</option>
          {Object.entries(CATEGORY_LABELS).map(([v, l]) => (
            <option key={v} value={v}>{l}</option>
          ))}
        </select>
      </div>

      {error && <p className="mb-4 text-sm text-red-600">{error}</p>}

      {loading ? (
        <div className="py-12 text-center text-gray-500">Loading vendors…</div>
      ) : filtered.length === 0 ? (
        <div className="py-12 text-center text-gray-500">No vendors match.</div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
              <tr>
                <th className="px-4 py-3">Vendor</th>
                <th className="px-4 py-3">Category</th>
                <th className="px-4 py-3">Department</th>
                <th className="px-4 py-3">Owner</th>
                <th className="px-4 py-3">Risk</th>
                <th className="px-4 py-3">Provenance</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {filtered.map((v) => (
                <>
                  <tr key={v.id} className="cursor-pointer hover:bg-gray-50" onClick={() => setExpanded(expanded === v.id ? null : v.id)}>
                    <td className="px-4 py-3">
                      <div className="font-medium text-gray-900">{v.name}</div>
                      <div className="text-xs text-gray-500">{v.ai_feature}</div>
                    </td>
                    <td className="px-4 py-3 text-gray-700">{CATEGORY_LABELS[v.category]}</td>
                    <td className="px-4 py-3 text-gray-700">{v.department}</td>
                    <td className="px-4 py-3 text-gray-700">{v.owner_user_id ? v.owner_user_id.slice(0, 8) : '—'}</td>
                    <td className="px-4 py-3">
                      <span className={`rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${riskTone(v.risk_score)}`}>
                        {v.risk_score}
                      </span>
                    </td>
                    <td className="px-4 py-3"><ProvenanceChip vendor={v} /></td>
                    <td className="px-4 py-3 text-right" onClick={(e) => e.stopPropagation()}>
                      <div className="flex justify-end gap-2">
                        <button className="text-xs text-indigo-600 hover:underline" onClick={() => setRequesting(v)}>Request</button>
                        <button className="text-xs text-indigo-600 hover:underline" onClick={() => setImporting(v)}>Import</button>
                      </div>
                    </td>
                  </tr>
                  {expanded === v.id && (
                    <tr key={`${v.id}-detail`}>
                      <td colSpan={7} className="p-0">
                        <VendorDetail vendor={v} />
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {requesting && (
        <RequestModal vendor={requesting} onClose={() => setRequesting(null)} onDone={() => { setRequesting(null); void load(); }} />
      )}
      {importing && (
        <ImportModal vendor={importing} onClose={() => setImporting(null)} onDone={() => { setImporting(null); void load(); }} />
      )}
    </div>
  );
}
