'use client';

// ─────────────────────────────────────────────────────────────────────
// ConnectWizard — 3-step Sentinel connect flow. Step 1 captures the
// workspace / table target, step 2 the data mapping (which Prompt Shields
// event types stream into that table), step 3 the Azure Monitor
// coordinates that turn on live forwarding. Submits to
// POST /api/v1/integrations/sentinel/connect.
//
// Step 3 is optional: skipping it connects in preview mode, where the
// dashboard shows a seeded event stream and nothing is sent to Azure. That
// lets an admin connect before their Sentinel admin has run the Bicep
// template. The Azure fields are all-or-nothing — the backend rejects a
// partial set, since a half-filled config would look connected while
// forwarding nothing.
//
// See docs/integrations/microsoft-sentinel/runbooks/customer-onboarding.md
// for where these values come from.
// ─────────────────────────────────────────────────────────────────────

import { useState } from 'react';
import { ChevronRight, Loader2, ShieldCheck } from 'lucide-react';
import {
  SENTINEL_EVENT_TYPES,
  sentinelApi,
  type SentinelConnectRequest,
  type SentinelEventType,
} from '@/lib/aispm/sentinel';
import type { IntegrationCardResponse } from '@/lib/types';
import { DataMapping } from './data-mapping';

interface ConnectWizardProps {
  onConnected: (card: IntegrationCardResponse) => void;
}

type Step = 1 | 2 | 3;

const STEP_LABELS: Record<Step, string> = {
  1: 'Workspace',
  2: 'Data mapping',
  3: 'Azure connection',
};

export function ConnectWizard({ onConnected }: ConnectWizardProps) {
  const [step, setStep] = useState<Step>(1);
  const [workspaceName, setWorkspaceName] = useState('');
  const [tableName, setTableName] = useState('PromptShieldsActivity_CL');
  const [enabledTypes, setEnabledTypes] = useState<Set<SentinelEventType>>(
    () => new Set(SENTINEL_EVENT_TYPES),
  );

  // Azure Monitor coordinates — from the Bicep deployment outputs and the
  // customer's app registration.
  const [azureTenantId, setAzureTenantId] = useState('');
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [dceUrl, setDceUrl] = useState('');
  const [dcrImmutableId, setDcrImmutableId] = useState('');

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const azureFields = [azureTenantId, clientId, clientSecret, dceUrl, dcrImmutableId];
  const azureComplete = azureFields.every((v) => v.trim().length > 0);
  const azureTouched = azureFields.some((v) => v.trim().length > 0);

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
      const body: SentinelConnectRequest = {
        workspace_name: workspaceName.trim(),
        table_name: tableName.trim() || 'PromptShieldsActivity_CL',
        enabled_event_types: Array.from(enabledTypes),
      };
      // Send the Azure block only when it is complete; a partial set is a
      // 422 from the backend, and omitting it entirely is the valid
      // preview-mode connect.
      if (azureComplete) {
        body.azure_tenant_id = azureTenantId.trim();
        body.client_id = clientId.trim();
        body.client_secret = clientSecret;
        body.dce_url = dceUrl.trim();
        body.dcr_immutable_id = dcrImmutableId.trim();
      }
      const card = await sentinelApi.connect(body);
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
        {([1, 2, 3] as Step[]).map((n) => (
          <span key={n} className="flex items-center gap-2">
            {n > 1 && (
              <ChevronRight className="h-3.5 w-3.5 text-slate-300" aria-hidden="true" />
            )}
            <span
              className={`flex h-5 w-5 items-center justify-center rounded-full ${
                step === n ? 'bg-indigo-600 text-white' : 'bg-slate-100 text-slate-500'
              }`}
            >
              {n}
            </span>
            <span className={step === n ? 'text-slate-800' : 'text-slate-400'}>
              {STEP_LABELS[n]}
            </span>
          </span>
        ))}
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
      ) : step === 2 ? (
        <div className="mt-5 space-y-4">
          <p className="text-xs text-slate-500">
            Choose which Prompt Shields event types map into{' '}
            <code className="font-mono text-slate-700">{tableName || 'PromptShieldsActivity_CL'}</code>.
          </p>
          <DataMapping selected={enabledTypes} onToggle={toggleType} />
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
              disabled={enabledTypes.size === 0}
              onClick={() => setStep(3)}
              className="inline-flex items-center gap-1 rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Next: Azure connection
              <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </div>
        </div>
      ) : (
        <div className="mt-5 space-y-4">
          <p className="text-xs text-slate-500">
            Paste the outputs from{' '}
            <code className="font-mono text-slate-700">sentinel-customer-setup.bicep</code> and
            your app registration. Leave these blank to connect in preview mode — the
            dashboard will show a seeded stream and nothing is sent to Azure.
          </p>

          <div>
            <label htmlFor="azure_tenant_id" className="block text-xs font-medium text-slate-700">
              Azure tenant ID
            </label>
            <input
              id="azure_tenant_id"
              type="text"
              value={azureTenantId}
              onChange={(e) => setAzureTenantId(e.target.value)}
              placeholder="11111111-2222-3333-4444-555555555555"
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 font-mono text-sm text-slate-900 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label htmlFor="client_id" className="block text-xs font-medium text-slate-700">
                Client ID
              </label>
              <input
                id="client_id"
                type="text"
                value={clientId}
                onChange={(e) => setClientId(e.target.value)}
                className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 font-mono text-sm text-slate-900 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>
            <div>
              <label htmlFor="client_secret" className="block text-xs font-medium text-slate-700">
                Client secret
              </label>
              <input
                id="client_secret"
                type="password"
                autoComplete="off"
                value={clientSecret}
                onChange={(e) => setClientSecret(e.target.value)}
                className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 font-mono text-sm text-slate-900 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
              <p className="mt-1 text-[11px] text-slate-500">
                Stored encrypted and never shown again.
              </p>
            </div>
          </div>

          <div>
            <label htmlFor="dce_url" className="block text-xs font-medium text-slate-700">
              Data Collection Endpoint URI
            </label>
            <input
              id="dce_url"
              type="url"
              value={dceUrl}
              onChange={(e) => setDceUrl(e.target.value)}
              placeholder="https://acme-dce.eastus-1.ingest.monitor.azure.com"
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 font-mono text-sm text-slate-900 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
          </div>

          <div>
            <label htmlFor="dcr_immutable_id" className="block text-xs font-medium text-slate-700">
              DCR immutable ID
            </label>
            <input
              id="dcr_immutable_id"
              type="text"
              value={dcrImmutableId}
              onChange={(e) => setDcrImmutableId(e.target.value)}
              placeholder="dcr-0123456789abcdef"
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 font-mono text-sm text-slate-900 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
          </div>

          {azureTouched && !azureComplete && (
            <p className="rounded-md bg-amber-50 px-3 py-2 text-[11px] text-amber-800 ring-1 ring-amber-100">
              Fill in every Azure field to enable live forwarding, or clear them all to
              connect in preview mode.
            </p>
          )}

          {error && (
            <p className="rounded-md bg-red-50 px-3 py-2 text-[11px] text-red-700 ring-1 ring-red-100">
              {error}
            </p>
          )}

          <div className="flex items-center justify-between">
            <button
              type="button"
              onClick={() => setStep(2)}
              className="text-xs font-medium text-slate-500 hover:text-slate-700"
            >
              Back
            </button>
            <button
              type="button"
              disabled={submitting || (azureTouched && !azureComplete)}
              onClick={handleSubmit}
              className="inline-flex items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
              {azureComplete ? 'Connect and start forwarding' : 'Connect in preview mode'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
