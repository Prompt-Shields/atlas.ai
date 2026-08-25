'use client';

// Admin teams view (PRO-58) — lists the tenant's synced Entra groups and,
// when one is selected, its members. Backed by GET /teams and
// GET /teams/{id}/members (PRO-57).

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import type { TeamMemberResponse, TeamResponse } from '@/lib/types';

export default function TeamsPage() {
  const [teams, setTeams] = useState<TeamResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<TeamResponse | null>(null);
  const [members, setMembers] = useState<TeamMemberResponse[]>([]);
  const [membersLoading, setMembersLoading] = useState(false);

  useEffect(() => {
    api
      .listTeams()
      .then((data: any) => setTeams(data.teams || []))
      .catch(() => setTeams([]))
      .finally(() => setLoading(false));
  }, []);

  function openTeam(team: TeamResponse) {
    setSelected(team);
    setMembersLoading(true);
    setMembers([]);
    api
      .listTeamMembers(team.id)
      .then((data: any) => setMembers(data.members || []))
      .catch(() => setMembers([]))
      .finally(() => setMembersLoading(false));
  }

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900">Teams</h1>
      <p className="mt-1 text-sm text-gray-500">Microsoft Entra groups synced from your directory.</p>

      <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Teams list */}
        <div className="overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-gray-200">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Team</th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Type</th>
                <th className="px-4 py-3 text-right text-xs font-medium uppercase text-gray-500">Members</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {teams.map((t) => (
                <tr
                  key={t.id}
                  onClick={() => openTeam(t)}
                  className={`cursor-pointer hover:bg-gray-50 ${selected?.id === t.id ? 'bg-primary-50' : ''}`}
                >
                  <td className="px-4 py-3">
                    <div className="text-sm font-medium text-gray-900">{t.display_name}</div>
                    {t.description && <div className="text-xs text-gray-500">{t.description}</div>}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-500">{t.group_type ?? '—'}</td>
                  <td className="px-4 py-3 text-right text-sm text-gray-700">{t.member_count}</td>
                </tr>
              ))}
              {!loading && teams.length === 0 && (
                <tr>
                  <td colSpan={3} className="px-4 py-8 text-center text-sm text-gray-500">
                    No teams synced yet.{' '}
                    <Link href="/dashboard/integrations" className="text-primary-600 hover:underline">
                      Connect Microsoft Entra ID
                    </Link>{' '}
                    to sync your directory groups.
                  </td>
                </tr>
              )}
              {loading && (
                <tr><td colSpan={3} className="px-4 py-8 text-center text-sm text-gray-400">Loading…</td></tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Selected team's members */}
        <div className="overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-gray-200">
          {!selected ? (
            <div className="px-4 py-8 text-center text-sm text-gray-400">Select a team to view its members.</div>
          ) : (
            <>
              <div className="border-b border-gray-200 bg-gray-50 px-4 py-3">
                <h2 className="text-sm font-semibold text-gray-900">{selected.display_name} · members</h2>
              </div>
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-2 text-left text-xs font-medium uppercase text-gray-500">Name</th>
                    <th className="px-4 py-2 text-left text-xs font-medium uppercase text-gray-500">Email</th>
                    <th className="px-4 py-2 text-left text-xs font-medium uppercase text-gray-500">Dept</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {members.map((m) => (
                    <tr key={m.id} className={m.is_active ? '' : 'opacity-50'}>
                      <td className="px-4 py-2 text-sm font-medium text-gray-900">{m.display_name}</td>
                      <td className="px-4 py-2 text-sm text-gray-500">{m.email ?? '—'}</td>
                      <td className="px-4 py-2 text-sm text-gray-500">{m.department ?? '—'}</td>
                    </tr>
                  ))}
                  {!membersLoading && members.length === 0 && (
                    <tr><td colSpan={3} className="px-4 py-6 text-center text-sm text-gray-500">No members in this team.</td></tr>
                  )}
                  {membersLoading && (
                    <tr><td colSpan={3} className="px-4 py-6 text-center text-sm text-gray-400">Loading…</td></tr>
                  )}
                </tbody>
              </table>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
