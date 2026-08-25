'use client';

// ─────────────────────────────────────────────────────────────────────
// CsvImportDialog — bulk-import AI use cases from a CSV.
//
// Two-step flow:
//   1. Choose a file (drag-drop OR file picker) → preview the row
//      count locally (no upload yet).
//   2. Click Import → POST to /api/v1/ai-inventory/import, show
//      imported / errors / warnings summary, leave the dialog open
//      so the admin can see the result before dismissing.
//
// Backend does the heavy lifting: validation is per-row and
// non-poisoning, so a partial-success outcome (e.g. "8 of 10
// imported, 2 had errors") is a first-class result rather than a
// generic 4xx.
// ─────────────────────────────────────────────────────────────────────

import { useRef, useState } from 'react';
import { api } from '@/lib/api';
import type { AiInventoryImportResponse } from '@/lib/types';

interface Props {
  open: boolean;
  onClose: () => void;
  /** Called once after a successful import so the parent can re-fetch. */
  onImported?: (result: AiInventoryImportResponse) => void;
}

export function CsvImportDialog({ open, onClose, onImported }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<AiInventoryImportResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  if (!open) return null;

  function reset() {
    setFile(null);
    setResult(null);
    setError(null);
    setBusy(false);
    if (fileInputRef.current) fileInputRef.current.value = '';
  }

  function close() {
    reset();
    onClose();
  }

  async function doImport() {
    if (!file) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const r = await api.importAiInventoryCsv(file);
      setResult(r);
      onImported?.(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Import failed.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 px-4"
      role="dialog"
      aria-modal="true"
      aria-label="Import AI inventory from CSV"
    >
      <div className="w-full max-w-xl rounded-lg bg-white shadow-xl">
        <header className="flex items-center justify-between border-b border-slate-200 px-5 py-3">
          <h2 className="text-base font-semibold text-slate-900">
            Import AI inventory from CSV
          </h2>
          <button
            type="button"
            onClick={close}
            className="rounded p-1 text-slate-500 hover:bg-slate-100 hover:text-slate-700"
            aria-label="Close"
          >
            ✕
          </button>
        </header>

        <div className="space-y-4 px-5 py-4">
          {!result && (
            <>
              <p className="text-sm text-slate-600">
                Need a template?{' '}
                <a
                  href={api.aiInventoryImportTemplateUrl()}
                  className="font-medium text-indigo-600 hover:text-indigo-800 underline"
                  download
                >
                  Download the CSV template
                </a>
                . Columns: <code>title</code>, <code>tool</code>,{' '}
                <code>department</code> (required) + <code>owner_email</code>,{' '}
                <code>status</code>, <code>risk_tier</code>,{' '}
                <code>data_classes</code> (semicolon-separated),{' '}
                <code>frequency</code>, <code>notes</code> (optional).
              </p>

              <FilePicker
                file={file}
                onFile={setFile}
                inputRef={fileInputRef}
              />

              {error && (
                <p className="rounded bg-red-50 px-3 py-2 text-xs text-red-800">
                  {error}
                </p>
              )}
            </>
          )}

          {result && (
            <ImportResultPanel result={result} />
          )}
        </div>

        <footer className="flex items-center justify-end gap-2 border-t border-slate-200 px-5 py-3">
          {result ? (
            <button
              type="button"
              onClick={close}
              className="rounded-md bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-700"
            >
              Done
            </button>
          ) : (
            <>
              <button
                type="button"
                onClick={close}
                className="rounded-md px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={doImport}
                disabled={!file || busy}
                className="rounded-md bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-slate-300"
              >
                {busy ? 'Importing…' : 'Import'}
              </button>
            </>
          )}
        </footer>
      </div>
    </div>
  );
}


// ─── File picker (drag-drop + click) ─────────────────────────────────


function FilePicker({
  file,
  onFile,
  inputRef,
}: {
  file: File | null;
  onFile: (f: File | null) => void;
  inputRef: React.RefObject<HTMLInputElement | null>;
}) {
  const [dragging, setDragging] = useState(false);

  return (
    <label
      className={`flex flex-col items-center justify-center rounded-md border-2 border-dashed px-6 py-8 text-center transition-colors ${
        dragging
          ? 'border-indigo-400 bg-indigo-50'
          : file
          ? 'border-slate-300 bg-slate-50'
          : 'border-slate-200 bg-slate-50 hover:bg-slate-100'
      }`}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        const f = e.dataTransfer.files?.[0];
        if (f) onFile(f);
      }}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".csv,text/csv"
        className="sr-only"
        onChange={(e) => onFile(e.target.files?.[0] || null)}
      />
      {file ? (
        <>
          <span className="text-sm font-medium text-slate-900">
            {file.name}
          </span>
          <span className="mt-1 text-xs text-slate-500">
            {(file.size / 1024).toFixed(1)} KB · click to replace
          </span>
        </>
      ) : (
        <>
          <span className="text-sm font-medium text-slate-700">
            Drag a CSV here, or click to choose
          </span>
          <span className="mt-1 text-xs text-slate-500">
            Up to 1000 rows · UTF-8 encoding
          </span>
        </>
      )}
    </label>
  );
}


// ─── Result panel ────────────────────────────────────────────────────


function ImportResultPanel({ result }: { result: AiInventoryImportResponse }) {
  const ok = result.imported > 0 && result.errors.length === 0;
  const partial = result.imported > 0 && result.errors.length > 0;
  const failed = result.imported === 0;

  return (
    <div className="space-y-3">
      <div
        className={`rounded-md px-3 py-2 text-sm ${
          ok
            ? 'bg-emerald-50 text-emerald-900'
            : partial
            ? 'bg-amber-50 text-amber-900'
            : 'bg-red-50 text-red-900'
        }`}
      >
        <strong className="font-semibold">
          {result.imported} imported
        </strong>
        {result.errors.length > 0 && (
          <> · {result.errors.length} error{result.errors.length === 1 ? '' : 's'}</>
        )}
        {result.warnings.length > 0 && (
          <> · {result.warnings.length} warning{result.warnings.length === 1 ? '' : 's'}</>
        )}
        {failed && (
          <p className="mt-1 text-xs">
            No rows imported. Fix the errors below and try again.
          </p>
        )}
      </div>

      {result.errors.length > 0 && (
        <details open className="rounded-md border border-red-100">
          <summary className="cursor-pointer bg-red-50 px-3 py-2 text-xs font-semibold text-red-900">
            Errors ({result.errors.length})
          </summary>
          <ul className="max-h-48 divide-y divide-red-50 overflow-auto px-3 py-2 text-xs text-red-900">
            {result.errors.map((e, i) => (
              <li key={i} className="py-1">
                <span className="font-mono">
                  {e.row_number === 0 ? 'batch' : `row ${e.row_number}`}
                </span>
                {e.field && <span> · {e.field}</span>}: {e.message}
              </li>
            ))}
          </ul>
        </details>
      )}

      {result.warnings.length > 0 && (
        <details className="rounded-md border border-amber-100">
          <summary className="cursor-pointer bg-amber-50 px-3 py-2 text-xs font-semibold text-amber-900">
            Warnings ({result.warnings.length})
          </summary>
          <ul className="max-h-48 divide-y divide-amber-50 overflow-auto px-3 py-2 text-xs text-amber-900">
            {result.warnings.map((w, i) => (
              <li key={i} className="py-1">
                <span className="font-mono">row {w.row_number}</span>: {w.message}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
