'use client';

import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import type { UserResponse } from '@/lib/types';

const ROLES = ['TENANT_ADMIN', 'ORG_ADMIN', 'ANALYST', 'VIEWER'];

export default function UsersPage() {
  const [users, setUsers] = useState<UserResponse[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);

  // Filters (mirror the GET /users query params from PRO-57).
  const [q, setQ] = useState('');
  const [role, setRole] = useState('');
  const [status, setStatus] = useState(''); // '', 'active', 'inactive'
  const [source, setSource] = useState(''); // '', 'sso', 'local'

  const load = useCallback(() => {
    setLoading(true);
    api
      .listUsers({
        q: q.trim() || undefined,
        role: role || undefined,
        is_active: status === '' ? undefined : status === 'active',
        source: source || undefined,
      })
      .then((data: any) => {
        setUsers(data.users || []);
        setTotal(data.total || 0);
      })
      .catch(() => {
        setUsers([]);
        setTotal(0);
      })
      .finally(() => setLoading(false));
  }, [q, role, status, source]);

  // Debounce so typing in search doesn't fire a request per keystroke.
  useEffect(() => {
    const t = setTimeout(load, 250);
    return () => clearTimeout(t);
  }, [load]);

  const selectCls =
    'rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500';

  return (
    <div>
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Users</h1>
        <span className="text-sm text-gray-500">{total} total</span>
      </div>

      {/* Filters */}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search name or email…"
          className={`${selectCls} min-w-[16rem] flex-1`}
          aria-label="Search users"
        />
        <select value={role} onChange={(e) => setRole(e.target.value)} className={selectCls} aria-label="Filter by role">
          <option value="">All roles</option>
          {ROLES.map((r) => (
            <option key={r} value={r}>{r}</option>
          ))}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)} className={selectCls} aria-label="Filter by status">
          <option value="">All statuses</option>
          <option value="active">Active</option>
          <option value="inactive">Inactive</option>
        </select>
        <select value={source} onChange={(e) => setSource(e.target.value)} className={selectCls} aria-label="Filter by source">
          <option value="">All sources</option>
          <option value="sso">SSO</option>
          <option value="local">Local</option>
        </select>
      </div>

      <div className="mt-4 overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-gray-200">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Name</th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Email</th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Roles</th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Source</th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {users.map((u) => (
              <tr key={u.id} className="hover:bg-gray-50">
                <td className="px-4 py-3 text-sm font-medium text-gray-900">{u.full_name}</td>
                <td className="px-4 py-3 text-sm text-gray-500">{u.email}</td>
                <td className="px-4 py-3">
                  {u.roles.map((r) => (
                    <span key={r} className="mr-1 inline-flex rounded-full bg-primary-50 px-2 py-0.5 text-xs font-medium text-primary-700">{r}</span>
                  ))}
                </td>
                <td className="px-4 py-3">
                  <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${u.auth_source === 'sso' ? 'bg-blue-100 text-blue-800' : 'bg-gray-100 text-gray-700'}`}>
                    {u.auth_source === 'sso' ? 'SSO' : 'Local'}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${u.is_active ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'}`}>
                    {u.is_active ? 'Active' : 'Inactive'}
                  </span>
                </td>
              </tr>
            ))}
            {!loading && users.length === 0 && (
              <tr><td colSpan={5} className="px-4 py-8 text-center text-sm text-gray-500">No users match these filters</td></tr>
            )}
            {loading && (
              <tr><td colSpan={5} className="px-4 py-8 text-center text-sm text-gray-400">Loading…</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
