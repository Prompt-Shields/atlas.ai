'use client';

// ─────────────────────────────────────────────────────────────────────
// Ardoq AI-Lens export — /dashboard/integrations/ardoq (issue #248)
//
// Admin-gated report/download page: a per-entity row-count manifest
// fetched from GET /integrations/ardoq/manifest, plus a "Download ZIP"
// button that streams GET /integrations/ardoq/export (a 9-file CSV
// bundle mapping the AI-SPM component graph to Ardoq's AI-Lens
// entity shapes). Not wired into the OAuth-based provider registry —
// it's a stateless report, not a connection to persist.
// ─────────────────────────────────────────────────────────────────────

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import type { ArdoqManifestResponse } from '@/lib/types';

const ENTITY_LABELS: Record<string, string> = {
  technical_capabilities: 'Technical Capabilities',
  applications: 'Applications',
  technology_products: 'Technology Products',
  technology_services: 'Technology Services',
  people: 'People',
  organizations: 'Organizations',
  data_stores: 'Data Stores',
  compliance_assessments: 'Compliance Assessments',
  references: 'References',
};

const ENTITY_ORDER = Object.keys(ENTITY_LABELS);

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function ArdoqExportPage() {
  const [manifest, setManifest] = useState<ArdoqManifestResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  const loadManifest = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.getArdoqManifest();
      setManifest(res);
    } catch {
      setError("Couldn't load the export manifest.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadManifest();
  }, [loadManifest]);

  const handleDownload = async () => {
    setDownloading(true);
    setError(null);
    try {
      const blob = await api.downloadArdoqExport();
      downloadBlob(blob, 'ardoq-ai-lens-export.zip');
    } catch {
      setError("Couldn't download the export ZIP.");
    } finally {
      setDownloading(false);
    }
  };

  const total = manifest
    ? Object.values(manifest.totals).reduce((sum, n) => sum + n, 0)
    : null;

  return (
    <div>
      <p className="text-xs text-gray-500">
        <Link href="/dashboard/integrations" className="hover:text-gray-700">
          Integrations
        </Link>{' '}
        / Ardoq AI-Lens Export
      </p>

      <div className="mt-2 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Ardoq AI-Lens Export</h1>
          <p className="mt-1 text-sm text-gray-600">
            Export the AI-SPM component graph — applications, models, owners,
            data stores, and their relationships — as a ZIP of 9 CSVs shaped
            for Ardoq&apos;s AI-Lens import.
          </p>
        </div>
        <button
          type="button"
          onClick={handleDownload}
          disabled={downloading || loading}
          className="rounded-md bg-primary-600 px-4 py-2 text-sm font-semibold text-white hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {downloading ? 'Preparing ZIP…' : 'Download ZIP'}
        </button>
      </div>

      {error && (
        <p className="mt-4 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 ring-1 ring-red-100">
          {error}
        </p>
      )}

      <div className="mt-6 overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-gray-200 text-sm">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
                Entity
              </th>
              <th className="px-4 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-500">
                Row count
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading && (
              <tr>
                <td colSpan={2} className="px-4 py-6 text-center text-gray-500">
                  Loading manifest…
                </td>
              </tr>
            )}
            {!loading &&
              manifest &&
              ENTITY_ORDER.map((key) => (
                <tr key={key}>
                  <td className="px-4 py-2 text-gray-900">{ENTITY_LABELS[key]}</td>
                  <td className="px-4 py-2 text-right font-mono text-gray-700">
                    {manifest.totals[key] ?? 0}
                  </td>
                </tr>
              ))}
          </tbody>
          {!loading && manifest && (
            <tfoot className="border-t border-gray-200 bg-gray-50">
              <tr>
                <td className="px-4 py-2 font-semibold text-gray-900">Total</td>
                <td className="px-4 py-2 text-right font-mono font-semibold text-gray-900">
                  {total}
                </td>
              </tr>
            </tfoot>
          )}
        </table>
      </div>

      {manifest && (
        <p className="mt-3 text-xs text-gray-500">
          Manifest generated{' '}
          {new Date(manifest.generated_at).toLocaleString(undefined, {
            dateStyle: 'medium',
            timeStyle: 'short',
          })}
          . The ZIP is regenerated fresh on each download, so its row counts
          may differ slightly if data changed since this manifest loaded.
        </p>
      )}
    </div>
  );
}
