'use client';

// ─────────────────────────────────────────────────────────────────────
// ConnectWizard — 2-step Sentinel connect flow. Step 1 captures the
// workspace / table target, step 2 is the data mapping (which Prompt
// Shields event types stream into that table). Submits to
// POST /api/v1/integrations/sentinel/connect. v1 makes no live call to
// Azure Monitor — see app/routers/sentinel_connect.py.
// ─────────────────────────────────────────────────────────────────────

import { useState } from 'react';
import { ChevronRight, Loader2, ShieldCheck } from 'lucide-react';
import {
  SENTINEL_EVENT_TYPES,
  sentinelApi,
  type SentinelEventType,
} from '@/lib/aispm/sentinel';
import type { IntegrationCardResponse } from '@/lib/types';
import { DataMapping } from './data-mapping';

interface ConnectWizardProps {
  onConnected: (card: IntegrationCardResponse) => void;
}

export function ConnectWizard({ onConnected }: ConnectWizardProps) {
  const [step, setStep] = useState<1 | 2>(1);
  const [workspaceName, setWorkspaceName] = useState('');
  const [tableName, setTableName] = useState('PromptShieldsActivity_CL');
  const [enabledTypes, setEnabledTypes] = useState<Set<SentinelEventType>>(
    () => new Set(SENTINEL_EVENT_TYPES),
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggleType = (type: SentinelEventType) => {
    setEnabledTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  };

  const handleSubmit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const card = await sentinelApi.connect({
        workspace_name: workspaceName.trim(),
        table_name: tableName.trim() || 'PromptShieldsActivity_CL',
        enabled_event_types: Array.from(enabledTypes),
      });
      onConnected(card);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not connect Sentinel');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="mx-auto max-w-xl rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="flex items-center gap-2">
        <ShieldCheck className="h-5 w-5 text-indigo-600" aria-hidden="true" />
        <h2 className="text-sm font-semibold text-slate-900">Connect Microsoft Sentinel</h2>
      </div>

      {/* Step indicator */}
      <div className="mt-4 flex items-center gap-2 text-xs font-medium">
        <span
          className={`flex h-5 w-5 items-center justify-center rounded-full ${
            step === 1 ? 'bg-indigo-600 text-white' : 'bg-slate-100 text-slate-500'
          }`}
        >
          1
        </span>
        <span className={step === 1 ? 'text-slate-800' : 'text-slate-400'}>Workspace</span>
        <ChevronRight className="h-3.5 w-3.5 text-slate-300" aria-hidden="true" />
        <span
          className={`flex h-5 w-5 items-center justify-center rounded-full ${
            step === 2 ? 'bg-indigo-600 text-white' : 'bg-slate-100 text-slate-500'
          }`}
        >
          2
        </span>
        <span className={step === 2 ? 'text-slate-800' : 'text-slate-400'}>Data mapping</span>
      </div>

      {step === 1 ? (
        <div className="mt-5 space-y-4">
          <div>
            <label htmlFor="workspace_name" className="block text-xs font-medium text-slate-700">
              Sentinel workspace label
            </label>
            <input
              id="workspace_name"
              type="text"
              value={workspaceName}
              onChange={(e) => setWorkspaceName(e.target.value)}
              placeholder="Acme Corp SOC"
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-900 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
            <p className="mt-1 text-[11px] text-slate-500">
              A customer-facing label — not validated against a live Log Analytics workspace in v1.
            </p>
          </div>
          <div>
            <label htmlFor="table_name" className="block text-xs font-medium text-slate-700">
              Target custom table
            </label>
            <input
              id="table_name"
              type="text"
              value={tableName}
              onChange={(e) => setTableName(e.target.value)}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 font-mono text-sm text-slate-900 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
          </div>
          <div className="flex justify-end">
            <button
              type="button"
              disabled={!workspaceName.trim()}
              onClick={() => setStep(2)}
              className="inline-flex items-center gap-1 rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Next: data mapping
              <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </div>
        </div>
      ) : (
        <div className="mt-5 space-y-4">
          <p className="text-xs text-slate-500">
            Choose which Prompt Shields event types map into{' '}
            <code className="font-mono text-slate-700">{tableName || 'PromptShieldsActivity_CL'}</code>.
          </p>
          <DataMapping selected={enabledTypes} onToggle={toggleType} />
          {error && (
            <p className="rounded-md bg-red-50 px-3 py-2 text-[11px] text-red-700 ring-1 ring-red-100">
              {error}
            </p>
          )}
          <div className="flex items-center justify-between">
            <button
              type="button"
              onClick={() => setStep(1)}
              className="text-xs font-medium text-slate-500 hover:text-slate-700"
            >
              Back
            </button>
            <button
              type="button"
              disabled={submitting || enabledTypes.size === 0}
              onClick={handleSubmit}
              className="inline-flex items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
              Connect Sentinel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
