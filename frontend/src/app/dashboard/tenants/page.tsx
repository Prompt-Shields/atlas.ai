'use client';

import { useEffect, useState, FormEvent } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import type { TenantResponse } from '@/lib/types';

export default function TenantsPage() {
  const [tenants, setTenants] = useState<TenantResponse[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [showInvite, setShowInvite] = useState(false);
  const [name, setName] = useState('');
  const [slug, setSlug] = useState('');
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteTenantId, setInviteTenantId] = useState('');
  const [message, setMessage] = useState('');

  useEffect(() => { loadTenants(); }, []);

  async function loadTenants() {
    try {
      const data = await api.listTenants();
      setTenants(data);
    } catch { /* user may not be super admin */ }
  }

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    try {
      await api.createTenant({ name, slug, description: '' });
      setShowCreate(false);
      setName(''); setSlug('');
      loadTenants();
      setMessage('Tenant created');
    } catch (err: any) { setMessage(err.message); }
  }

  async function handleInvite(e: FormEvent) {
    e.preventDefault();
    try {
      await api.createInvite({ email: inviteEmail, tenant_id: inviteTenantId, role: 'TENANT_ADMIN' });
      setShowInvite(false);
      setInviteEmail(''); setInviteTenantId('');
      setMessage('Invite sent');
    } catch (err: any) { setMessage(err.message); }
  }

  return (
    <div>
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Tenants</h1>
        <div className="flex gap-2">
          <button onClick={() => setShowInvite(!showInvite)} className="rounded-lg bg-green-600 px-4 py-2 text-sm text-white hover:bg-green-700">Invite Tenant</button>
          <button onClick={() => setShowCreate(!showCreate)} className="rounded-lg bg-primary-600 px-4 py-2 text-sm text-white hover:bg-primary-700">Create Tenant</button>
        </div>
      </div>

      {message && <div className="mt-4 rounded-lg bg-blue-50 p-3 text-sm text-blue-700">{message}</div>}

      {showCreate && (
        <form onSubmit={handleCreate} className="mt-4 rounded-lg bg-white p-4 shadow-sm ring-1 ring-gray-200">
          <h3 className="font-semibold">Create Tenant</h3>
          <div className="mt-3 grid grid-cols-2 gap-3">
            <input value={name} onChange={e => setName(e.target.value)} placeholder="Tenant Name" required className="rounded border px-3 py-2 text-sm" />
            <input value={slug} onChange={e => setSlug(e.target.value)} placeholder="slug" required pattern="[a-z0-9-]+" className="rounded border px-3 py-2 text-sm" />
          </div>
          <button type="submit" className="mt-3 rounded bg-primary-600 px-4 py-2 text-sm text-white">Create</button>
        </form>
      )}

      {showInvite && (
        <form onSubmit={handleInvite} className="mt-4 rounded-lg bg-white p-4 shadow-sm ring-1 ring-gray-200">
          <h3 className="font-semibold">Invite Tenant Admin</h3>
          <div className="mt-3 grid grid-cols-2 gap-3">
            <input type="email" value={inviteEmail} onChange={e => setInviteEmail(e.target.value)} placeholder="Email" required className="rounded border px-3 py-2 text-sm" />
            <select value={inviteTenantId} onChange={e => setInviteTenantId(e.target.value)} required className="rounded border px-3 py-2 text-sm">
              <option value="">Select Tenant</option>
              {tenants.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </div>
          <button type="submit" className="mt-3 rounded bg-green-600 px-4 py-2 text-sm text-white">Send Invite</button>
        </form>
      )}

      <div className="mt-6 overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-gray-200">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Name</th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Slug</th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Status</th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Created</th>
              <th className="px-4 py-3 text-right text-xs font-medium uppercase text-gray-500"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {tenants.map(t => (
              <tr key={t.id} className="hover:bg-gray-50">
                <td className="px-4 py-3 text-sm font-medium text-gray-900">{t.name}</td>
                <td className="px-4 py-3 text-sm text-gray-500">{t.slug}</td>
                <td className="px-4 py-3">
                  <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${t.is_active ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-600'}`}>
                    {t.is_active ? 'Active' : 'Inactive'}
                  </span>
                </td>
                <td className="px-4 py-3 text-sm text-gray-500">{new Date(t.created_at).toLocaleDateString()}</td>
                <td className="px-4 py-3 text-right">
                  <Link href={`/dashboard/tenants/${t.id}`} className="text-sm font-medium text-primary-600 hover:text-primary-800">
                    Manage
                  </Link>
                </td>
              </tr>
            ))}
            {tenants.length === 0 && (
              <tr><td colSpan={5} className="px-4 py-8 text-center text-sm text-gray-500">No tenants found</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
