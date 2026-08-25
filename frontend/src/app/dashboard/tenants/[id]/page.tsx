'use client';

import { useEffect, useState, FormEvent } from 'react';
import { useParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import type { TenantResponse, OrgResponse } from '@/lib/types';

export default function TenantDetailPage() {
  const params = useParams();
  const router = useRouter();
  const tenantId = params.id as string;

  const [tenant, setTenant] = useState<TenantResponse | null>(null);
  const [orgs, setOrgs] = useState<OrgResponse[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [orgName, setOrgName] = useState('');
  const [orgSlug, setOrgSlug] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  async function loadTenantData() {
    try {
      const [tenantData, orgData] = await Promise.all([
        api.getTenant(tenantId),
        api.listOrgs(tenantId),
      ]);
      setTenant(tenantData);
      setOrgs(orgData);
    } catch (err: any) {
      setError(err.message || 'Failed to load tenant');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadTenantData();
  }, [tenantId]);

  async function handleCreateOrg(e: FormEvent) {
    e.preventDefault();
    setMessage('');
    setError('');
    try {
      await api.createOrg(tenantId, { name: orgName, slug: orgSlug });
      setShowCreate(false);
      setOrgName('');
      setOrgSlug('');
      setMessage('Organisation created successfully');
      const orgData = await api.listOrgs(tenantId);
      setOrgs(orgData);
    } catch (err: any) {
      setError(err.message || 'Failed to create organisation');
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="animate-pulse text-gray-500">Loading tenant...</div>
      </div>
    );
  }

  if (!tenant) {
    return (
      <div className="py-8 text-center">
        <p className="text-red-600">{error || 'Tenant not found'}</p>
        <Link href="/dashboard/tenants" className="mt-4 inline-block text-sm text-primary-600 hover:text-primary-800">
          Back to Tenants
        </Link>
      </div>
    );
  }

  return (
    <div>
      {/* Header */}
      <div className="mb-6">
        <Link href="/dashboard/tenants" className="text-sm text-gray-500 hover:text-gray-700">
          &larr; Back to Tenants
        </Link>
        <div className="mt-2 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">{tenant.name}</h1>
            <p className="mt-1 text-sm text-gray-500">
              Slug: <span className="font-mono">{tenant.slug}</span>
              <span className="mx-2">&middot;</span>
              <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${tenant.is_active ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-600'}`}>
                {tenant.is_active ? 'Active' : 'Inactive'}
              </span>
            </p>
          </div>
        </div>
      </div>

      {message && (
        <div className="mb-4 rounded-lg bg-green-50 p-3 text-sm text-green-700">{message}</div>
      )}
      {error && (
        <div className="mb-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</div>
      )}

      {/* Organisations Section */}
      <div className="rounded-lg bg-white shadow-sm ring-1 ring-gray-200">
        <div className="flex items-center justify-between border-b border-gray-200 px-4 py-4">
          <h2 className="text-lg font-semibold text-gray-900">Organisations</h2>
          <button
            onClick={() => { setShowCreate(!showCreate); setError(''); }}
            className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700"
          >
            {showCreate ? 'Cancel' : 'Create Organisation'}
          </button>
        </div>

        {showCreate && (
          <form onSubmit={handleCreateOrg} className="border-b border-gray-200 bg-gray-50 p-4">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div>
                <label htmlFor="orgName" className="mb-1 block text-sm font-medium text-gray-700">
                  Organisation Name
                </label>
                <input
                  id="orgName"
                  value={orgName}
                  onChange={e => setOrgName(e.target.value)}
                  placeholder="e.g. Engineering"
                  required
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                />
              </div>
              <div>
                <label htmlFor="orgSlug" className="mb-1 block text-sm font-medium text-gray-700">
                  Slug
                </label>
                <input
                  id="orgSlug"
                  value={orgSlug}
                  onChange={e => setOrgSlug(e.target.value)}
                  placeholder="e.g. engineering"
                  required
                  pattern="[a-z0-9-]+"
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                />
                <p className="mt-1 text-xs text-gray-500">Lowercase letters, numbers, and hyphens only</p>
              </div>
            </div>
            <button
              type="submit"
              className="mt-3 rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700"
            >
              Create
            </button>
          </form>
        )}

        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Name</th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Slug</th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Status</th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Created</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {orgs.map(org => (
              <tr key={org.id} className="hover:bg-gray-50">
                <td className="px-4 py-3 text-sm font-medium text-gray-900">{org.name}</td>
                <td className="px-4 py-3 text-sm font-mono text-gray-500">{org.slug}</td>
                <td className="px-4 py-3">
                  <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${org.is_active ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-600'}`}>
                    {org.is_active ? 'Active' : 'Inactive'}
                  </span>
                </td>
                <td className="px-4 py-3 text-sm text-gray-500">{new Date(org.created_at).toLocaleDateString()}</td>
              </tr>
            ))}
            {orgs.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-sm text-gray-500">
                  No organisations yet. Create one to get started.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
