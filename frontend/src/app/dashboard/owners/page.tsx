'use client';

// ─────────────────────────────────────────────────────────────────────
// Owners — AI use-case owner roster (atlas §3.8)
//
// Table view of every person who owns at least one registered AI use
// case. Sortable by owner-risk score. Per-row "View profile" routes
// to a per-person page (deferred — links into /dashboard/registry
// filtered by owner for now).
// ─────────────────────────────────────────────────────────────────────

import { useMemo, useState } from 'react';
import Link from 'next/link';
import {
  OWNERS,
  ORG,
  type OwnerProfile,
} from '@/lib/curated-demo-data';

type SortKey = 'name' | 'department' | 'ownedUseCaseCount' | 'riskOwnerScore';

function riskColour(score: number) {
  if (score >= 40) return 'bg-red-50 text-red-700 ring-red-200';
  if (score >= 20) return 'bg-amber-50 text-amber-700 ring-amber-200';
  return 'bg-emerald-50 text-emerald-700 ring-emerald-200';
}

export default function OwnersPage() {
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<SortKey>('riskOwnerScore');
  const [direction, setDirection] = useState<'asc' | 'desc'>('desc');

  const filtered = useMemo<OwnerProfile[]>(() => {
    const q = search.trim().toLowerCase();
    const subset = !q
      ? OWNERS
      : OWNERS.filter(
          (o) =>
            o.name.toLowerCase().includes(q) ||
            o.email.toLowerCase().includes(q) ||
            o.department.toLowerCase().includes(q),
        );
    return [...subset].sort((a, b) => {
      const av = a[sort];
      const bv = b[sort];
      if (typeof av === 'number' && typeof bv === 'number') {
        return direction === 'asc' ? av - bv : bv - av;
      }
      return direction === 'asc'
        ? String(av).localeCompare(String(bv))
        : String(bv).localeCompare(String(av));
    });
  }, [search, sort, direction]);

  const flip = (k: SortKey) => {
    if (sort === k) setDirection(direction === 'asc' ? 'desc' : 'asc');
    else {
      setSort(k);
      setDirection(k === 'name' || k === 'department' ? 'asc' : 'desc');
    }
  };

  const totals = useMemo(() => {
    let useCases = 0;
    let violations = 0;
    let untrained = 0;
    for (const o of OWNERS) {
      useCases += o.ownedUseCaseCount;
      violations += o.recentViolationCount;
      if (!o.trainingCompleteAt) untrained += 1;
    }
    return { useCases, violations, untrained };
  }, []);

  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Owners</h1>
          <p className="mt-1 text-sm text-gray-600">
            {ORG.name} — every person who owns a registered AI use case
          </p>
        </div>
        <Link
          href="/dashboard/owners/inference"
          className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
        >
          Suggest owners →
        </Link>
      </div>

      {/* Hero tiles */}
      <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Owners
          </p>
          <p className="mt-2 text-3xl font-bold text-gray-900">
            {OWNERS.length}
          </p>
        </div>
        <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Use cases owned
          </p>
          <p className="mt-2 text-3xl font-bold text-gray-900">
            {totals.useCases}
          </p>
        </div>
        <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Recent violations
          </p>
          <p className="mt-2 text-3xl font-bold text-amber-600">
            {totals.violations}
          </p>
        </div>
        <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Untrained
          </p>
          <p className="mt-2 text-3xl font-bold text-red-600">
            {totals.untrained}
          </p>
        </div>
      </div>

      {/* Search */}
      <div className="mt-6 max-w-md">
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search by name, email, or department…"
          className="w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-900 placeholder:text-gray-400 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
        />
      </div>

      {/* Table */}
      <div className="mt-6 overflow-hidden rounded-xl bg-white shadow-sm ring-1 ring-gray-200">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-100 text-sm">
            <thead>
              <tr className="bg-gray-50 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
                <th
                  className="cursor-pointer px-4 py-2 hover:text-gray-900"
                  onClick={() => flip('name')}
                >
                  Owner {sort === 'name' && (direction === 'asc' ? '↑' : '↓')}
                </th>
                <th
                  className="cursor-pointer px-4 py-2 hover:text-gray-900"
                  onClick={() => flip('department')}
                >
                  Department{' '}
                  {sort === 'department' && (direction === 'asc' ? '↑' : '↓')}
                </th>
                <th
                  className="cursor-pointer px-4 py-2 hover:text-gray-900"
                  onClick={() => flip('ownedUseCaseCount')}
                >
                  Use cases{' '}
                  {sort === 'ownedUseCaseCount' &&
                    (direction === 'asc' ? '↑' : '↓')}
                </th>
                <th className="px-4 py-2">Recent violations</th>
                <th className="px-4 py-2">Trained</th>
                <th
                  className="cursor-pointer px-4 py-2 hover:text-gray-900"
                  onClick={() => flip('riskOwnerScore')}
                >
                  Owner risk{' '}
                  {sort === 'riskOwnerScore' &&
                    (direction === 'asc' ? '↑' : '↓')}
                </th>
                <th className="px-4 py-2"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {filtered.map((o) => (
                <tr key={o.id} className="hover:bg-gray-50">
                  <td className="px-4 py-3">
                    <div className="font-medium text-gray-900">{o.name}</div>
                    <div className="text-xs text-gray-500">{o.email}</div>
                  </td>
                  <td className="px-4 py-3 text-gray-700">{o.department}</td>
                  <td className="px-4 py-3 text-gray-900 tabular-nums">
                    {o.ownedUseCaseCount}
                  </td>
                  <td className="px-4 py-3 text-gray-900 tabular-nums">
                    {o.recentViolationCount > 0 ? (
                      <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700 ring-1 ring-amber-200">
                        {o.recentViolationCount}
                      </span>
                    ) : (
                      <span className="text-gray-400">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-600">
                    {o.trainingCompleteAt ? (
                      <span className="text-emerald-700">
                        ✓ {o.trainingCompleteAt}
                      </span>
                    ) : (
                      <span className="text-red-700">✗ Not yet</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset tabular-nums ${riskColour(o.riskOwnerScore)}`}
                    >
                      {o.riskOwnerScore}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Link
                      href={`/dashboard/registry?owner=${encodeURIComponent(o.name)}`}
                      className="text-xs font-medium text-primary-600 hover:text-primary-700"
                    >
                      View →
                    </Link>
                  </td>
                </tr>
              ))}
              {filtered.length === 0 && (
                <tr>
                  <td
                    colSpan={7}
                    className="px-4 py-8 text-center text-sm text-gray-500"
                  >
                    No owners match.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <p className="mt-4 text-xs text-gray-500">
        Owner risk score is a 0–100 composite: weight × recent violations +
        weight × untrained + weight × unreviewed use cases. Lower is better.
      </p>
    </div>
  );
}
