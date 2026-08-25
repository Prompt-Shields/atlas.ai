'use client';

// ─────────────────────────────────────────────────────────────────────
// Discover · Defender for Cloud Apps import —
// /dashboard/discover/defender-import  (M3, issue #243)
//
// Paste a Defender for Cloud Apps export (no real OCR in v1 — see
// `services/defender_import_service.py`'s seeded `KNOWN_APPS` catalog);
// the backend extracts + enriches app rows with risk/compliance data.
// Accepting a candidate promotes it into the AI inventory as a SaaS
// vendor. Wired to /api/v1/discover/defender-import via
// lib/aispm/defender-import.ts.
// ─────────────────────────────────────────────────────────────────────

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import {
  defenderImportApi,
  SAMPLE_DEFENDER_EXPORT,
  type DiscoveredDefenderApp,
} from '@/lib/aispm/defender-import';
import { DefenderImportAppList } from '@/components/spm/defender-import';

export default function DefenderImportPage() {
  const [apps, setApps] = useState<DiscoveredDefenderApp[]>([]);
  const [rawText, setRawText] = useState('');
  const [loading, setLoading] = useState(true);
  const [importing, setImporting] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<{ matched: number; created: number; updated: number } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setApps((await defenderImportApi.list()).apps);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load imported apps');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const runImport = async () => {
    setImporting(true);
    setError(null);
    try {
      const result = await defenderImportApi.import({ raw_text: rawText || null });
      setLastResult({ matched: result.matched_lines, created: result.created, updated: result.updated });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Import failed');
    } finally {
      setImporting(false);
    }
  };

  const accept = async (app: DiscoveredDefenderApp) => {
    setBusyId(app.id);
    setError(null);
    try {
      await defenderImportApi.accept(app.id);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to accept app');
    } finally {
      setBusyId(null);
    }
  };

  const dismiss = async (app: DiscoveredDefenderApp) => {
    setBusyId(app.id);
    setError(null);
    try {
      await defenderImportApi.dismiss(app.id);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to dismiss app');
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="p-8">
      <div className="mb-6">
        <Link href="/dashboard/discover" className="text-sm text-indigo-600 hover:underline">
          ← Discover
        </Link>
        <h1 className="mt-1 text-3xl font-semibold">Defender for Cloud Apps Import</h1>
        <p className="text-gray-600">
          Paste a Defender for Cloud Apps discovered-apps export. Recognised apps are
          extracted and enriched with risk and compliance data — accept a candidate to
          add it to the AI inventory.
        </p>
      </div>

      <div className="mb-6 rounded-lg border border-gray-200 bg-white p-4">
        <label htmlFor="defender-export" className="mb-2 block text-sm font-medium text-gray-700">
          Export text
        </label>
        <textarea
          id="defender-export"
          className="h-32 w-full rounded border border-gray-300 p-2 font-mono text-xs text-gray-800"
          placeholder={SAMPLE_DEFENDER_EXPORT}
          value={rawText}
          onChange={(e) => setRawText(e.target.value)}
        />
        <div className="mt-3 flex items-center gap-3">
          <button
            className="rounded bg-indigo-600 px-3 py-2 text-sm text-white hover:bg-indigo-500 disabled:opacity-50"
            disabled={importing}
            onClick={runImport}
          >
            {importing ? 'Importing…' : 'Import'}
          </button>
          <button
            type="button"
            className="text-xs text-indigo-600 hover:underline"
            onClick={() => setRawText(SAMPLE_DEFENDER_EXPORT)}
          >
            Use sample export
          </button>
          {lastResult && (
            <span className="text-xs text-gray-500">
              {lastResult.matched} line{lastResult.matched === 1 ? '' : 's'} matched
              ({lastResult.created} new, {lastResult.updated} updated)
            </span>
          )}
        </div>
      </div>

      {error && <p className="mb-4 text-sm text-red-600">{error}</p>}

      {loading ? (
        <div className="py-12 text-center text-gray-500">Loading imported apps…</div>
      ) : apps.length === 0 ? (
        <div className="py-12 text-center text-gray-500">
          No apps imported yet. Paste an export above (or use the sample) and click Import.
        </div>
      ) : (
        <DefenderImportAppList
          apps={apps}
          busyId={busyId}
          onAccept={(app) => void accept(app)}
          onDismiss={(app) => void dismiss(app)}
        />
      )}
    </div>
  );
}
