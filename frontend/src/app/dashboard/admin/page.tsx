'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import type { LLMUsageSummary, TenantResponse } from '@/lib/types';

export default function AdminPage() {
  const [tenants, setTenants] = useState<TenantResponse[]>([]);
  const [selectedTenant, setSelectedTenant] = useState('');
  const [selectedOrg, setSelectedOrg] = useState('');
  const [orgs, setOrgs] = useState<any[]>([]);
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(false);
  const [pipelineResult, setPipelineResult] = useState<any>(null);
  const [usageSummary, setUsageSummary] = useState<LLMUsageSummary | null>(null);

  useEffect(() => {
    api.listTenants().then(setTenants).catch(() => {});
    api.getLLMUsageSummary(30).then(setUsageSummary).catch(() => {});
  }, []);

  useEffect(() => {
    if (selectedTenant) {
      api.listOrgs(selectedTenant).then(setOrgs).catch(() => setOrgs([]));
    }
  }, [selectedTenant]);

  async function handleRunPipeline() {
    if (!selectedTenant || !selectedOrg) {
      setMessage('Select a tenant and org first');
      return;
    }
    setLoading(true);
    setPipelineResult(null);
    try {
      const result = await api.runTestPipeline(selectedTenant, selectedOrg);
      setPipelineResult(result);
      setMessage('Pipeline completed');
    } catch (err: any) {
      setMessage(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function handlePurge() {
    if (!confirm('Are you sure you want to purge ALL test data? This cannot be undone.')) return;
    setLoading(true);
    try {
      const result = await api.purgeTestData(true, selectedTenant || undefined);
      setMessage(`Purged: ${JSON.stringify(result.counts)}`);
    } catch (err: any) {
      setMessage(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900">Admin Tools</h1>
      <p className="mt-1 text-sm text-gray-500">Super admin only — test pipeline execution and data management</p>

      {message && <div className="mt-4 rounded-lg bg-blue-50 p-3 text-sm text-blue-700">{message}</div>}

      {/* Tenant/Org selector */}
      <div className="mt-6 rounded-lg bg-white p-4 shadow-sm ring-1 ring-gray-200">
        <h2 className="font-semibold text-gray-900">Select Context</h2>
        <div className="mt-3 grid grid-cols-2 gap-3">
          <select
            value={selectedTenant}
            onChange={e => { setSelectedTenant(e.target.value); setSelectedOrg(''); }}
            className="rounded border px-3 py-2 text-sm"
          >
            <option value="">Select Tenant</option>
            {tenants.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
          <select
            value={selectedOrg}
            onChange={e => setSelectedOrg(e.target.value)}
            className="rounded border px-3 py-2 text-sm"
          >
            <option value="">Select Organisation</option>
            {orgs.map((o: any) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select>
        </div>
      </div>

      {/* Test Tools */}
      <div className="mt-6 grid gap-4 md:grid-cols-2">
        <div className="rounded-lg bg-white p-6 shadow-sm ring-1 ring-gray-200">
          <h2 className="text-lg font-semibold text-gray-900">🧪 Run E2E Test Pipeline</h2>
          <p className="mt-2 text-sm text-gray-600">
            Executes: Blob insertion → Risk analysis → Correlation → Dispatch.
            All data marked as TEST.
          </p>
          <button
            onClick={handleRunPipeline}
            disabled={loading || !selectedTenant || !selectedOrg}
            className="mt-4 rounded-lg bg-primary-600 px-4 py-2 text-sm text-white hover:bg-primary-700 disabled:opacity-50"
          >
            {loading ? 'Running...' : 'Run Pipeline'}
          </button>
        </div>

        <div className="rounded-lg bg-white p-6 shadow-sm ring-1 ring-red-200">
          <h2 className="text-lg font-semibold text-red-700">🗑️ Purge Test Data</h2>
          <p className="mt-2 text-sm text-gray-600">
            Permanently deletes all records marked as test data.
            Requires confirmation. Audit logged.
          </p>
          <button
            onClick={handlePurge}
            disabled={loading}
            className="mt-4 rounded-lg bg-red-600 px-4 py-2 text-sm text-white hover:bg-red-700 disabled:opacity-50"
          >
            {loading ? 'Purging...' : 'Purge Test Data'}
          </button>
        </div>
      </div>

      {/* Pipeline Result */}
      {pipelineResult && (
        <div className="mt-6 rounded-lg bg-gray-50 p-4">
          <h3 className="font-semibold text-gray-900">Pipeline Result</h3>
          <pre className="mt-2 overflow-auto text-xs">{JSON.stringify(pipelineResult, null, 2)}</pre>
        </div>
      )}

      {/* LLM Usage Summary */}
      {usageSummary && (
        <div className="mt-6 rounded-lg bg-white p-6 shadow-sm ring-1 ring-gray-200">
          <h2 className="text-lg font-semibold text-gray-900">LLM Usage (Last 30 Days)</h2>
          <div className="mt-4 grid grid-cols-2 gap-4 md:grid-cols-4">
            <div>
              <p className="text-sm text-gray-500">Total Requests</p>
              <p className="text-2xl font-bold">{usageSummary.total_requests}</p>
            </div>
            <div>
              <p className="text-sm text-gray-500">Total Tokens</p>
              <p className="text-2xl font-bold">{usageSummary.total_tokens.toLocaleString()}</p>
            </div>
            <div>
              <p className="text-sm text-gray-500">Estimated Cost</p>
              <p className="text-2xl font-bold text-green-700">${usageSummary.total_estimated_cost_usd.toFixed(4)}</p>
            </div>
            <div>
              <p className="text-sm text-gray-500">Period</p>
              <p className="text-sm">{new Date(usageSummary.period_start).toLocaleDateString()} - {new Date(usageSummary.period_end).toLocaleDateString()}</p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
