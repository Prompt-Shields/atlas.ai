// ─────────────────────────────────────────────────────────────────────
// Deployment / Agent fleet — atlas dashboard §3.11
//
// Role:
//   "Is the product real in our environment?" A pilot IT lead validated this
//   as the trust-gap closer — when an IT lead is mid-evaluation,
//   this is the page that proves the agent is running everywhere
//   it's supposed to be running, on the right version, recently in
//   contact with Intune.
//
// Layout:
//   1. Header
//   2. Three stat tiles row — agents online (with progress bar),
//      agent version, last Intune sync
//   3. Two-column row — Platform breakdown / Offline agents
//   4. Pilot-prerequisite callout (amber band) — surfaces the
//      Hardened Windows ARM 64 build as the gating item for
//      go-live
//
// Data:
//   frontend/src/lib/curated-demo-data.ts → DEPLOYMENT.
//   When the M5 worker tenant helper (PR #3) ships its agent-fleet
//   collector, swap the static import for an async loader against
//   /api/v1/agents — the JSX doesn't change.
//
// Notes:
//   Pure render — no client interactivity needed, so this is a
//   server component by default (no 'use client').
// ─────────────────────────────────────────────────────────────────────

import Link from 'next/link';
import { DEPLOYMENT, ORG } from '@/lib/curated-demo-data';

// ─── Sub-components ───────────────────────────────────────────────────

function OnlineTile() {
  const pct =
    Math.round((DEPLOYMENT.agentsOnline / ORG.totalUsers) * 1000) / 10;
  // Lagging threshold mirrors the Coaching / Overview convention:
  // ≤60% = amber, otherwise emerald. 97.5% → emerald.
  const tone = pct <= 60 ? 'bg-amber-500' : 'bg-emerald-500';
  return (
    <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
      <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
        Agents online
      </p>
      <div className="mt-2 flex items-baseline gap-2">
        <p className="text-3xl font-bold text-gray-900 tabular-nums">
          {DEPLOYMENT.agentsOnline} / {DEPLOYMENT.agentsTotal}
        </p>
        <p className="text-sm font-medium text-gray-500">{pct}%</p>
      </div>
      <div
        className="mt-3 h-2 w-full rounded-full bg-gray-100"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`Agents online: ${pct}%`}
      >
        <div
          className={`h-2 rounded-full ${tone}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="mt-2 text-xs text-gray-500">
        Healthy when ≥ 95% of seats checked in within the last hour.
      </p>
    </div>
  );
}

function VersionTile() {
  const upToDate = DEPLOYMENT.versionStatus === 'Up to date';
  return (
    <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
      <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
        Agent version
      </p>
      <p className="mt-2 text-3xl font-bold text-gray-900">
        {DEPLOYMENT.agentVersion}
      </p>
      <p
        className={
          upToDate
            ? 'mt-1 text-xs font-medium text-emerald-600'
            : 'mt-1 text-xs font-medium text-amber-600'
        }
      >
        {upToDate ? '✓' : '⚠'} {DEPLOYMENT.versionStatus}
      </p>
      <p className="mt-2 text-xs text-gray-500">
        Rolls out via Intune managed-app channel — no user prompt
        required.
      </p>
    </div>
  );
}

function IntuneSyncTile() {
  return (
    <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
      <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
        Last Intune sync
      </p>
      <p className="mt-2 text-3xl font-bold text-gray-900">
        {DEPLOYMENT.lastIntuneSync}
      </p>
      <p className="mt-1 text-xs font-medium text-emerald-600">
        ✓ within the 1-hour window
      </p>
      <p className="mt-2 text-xs text-gray-500">
        Atlas reads Intune device + assignment state every 30 min
        when a tenant is connected.
      </p>
    </div>
  );
}

function PlatformBreakdown() {
  const total = DEPLOYMENT.platforms.reduce((s, p) => s + p.count, 0);
  return (
    <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-900">
          Platform breakdown
        </h3>
        <span className="text-xs text-gray-500">{total} agents</span>
      </div>
      <ul className="mt-4 space-y-3">
        {DEPLOYMENT.platforms.map((p) => {
          const pct = total > 0 ? Math.round((p.count / total) * 100) : 0;
          return (
            <li key={p.name}>
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm text-gray-800">{p.name}</span>
                <span className="text-xs font-medium text-gray-900 tabular-nums">
                  {p.count}
                </span>
              </div>
              <div
                className="mt-1 h-1.5 w-full rounded-full bg-gray-100"
                aria-hidden="true"
              >
                <div
                  className="h-1.5 rounded-full bg-gray-700"
                  style={{ width: `${pct}%` }}
                />
              </div>
            </li>
          );
        })}
      </ul>
      <p className="mt-4 text-xs text-gray-500">
        Windows ARM 64 is the primary target — Surface devices are the
        majority of foundation issued endpoints. macOS and Windows x64
        coverage rolls forward as the Hardened ARM 64 build clears
        pilot.
      </p>
    </div>
  );
}

function OfflineAgents() {
  const offline = DEPLOYMENT.offlineAgents;
  return (
    <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-900">
          Offline agents
        </h3>
        <span className="text-xs text-gray-500">
          {offline.length} of {ORG.totalUsers}
        </span>
      </div>
      {offline.length === 0 ? (
        <p className="mt-4 text-sm text-emerald-700">
          ✓ Every assigned agent has checked in within the last hour.
        </p>
      ) : (
        <ul className="mt-4 space-y-3">
          {offline.map((a) => (
            <li
              key={a.user}
              className="rounded-md bg-gray-50 px-3 py-2 ring-1 ring-gray-100"
            >
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm font-medium text-gray-900">
                  {a.user}
                </span>
                <span className="text-xs text-gray-500">
                  Last seen {a.lastSeen}
                </span>
              </div>
              <p className="mt-1 text-xs text-gray-600">{a.reason}</p>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-4 text-xs text-gray-500">
        Offline agents are excluded from the active-user count on the
        Overview tile. Offboarded users auto-suppress; sleep-mode
        warnings raise when last-seen exceeds 7 days.
      </p>
    </div>
  );
}

function PilotPrerequisite() {
  return (
    <div className="rounded-xl bg-amber-50 px-5 py-4 ring-1 ring-amber-100">
      <p className="text-sm text-amber-900">
        <strong>Go-live gating item.</strong> The Hardened Windows ARM 64
        build is the prerequisite for production rollout — tracked
        separately in the pilot board. macOS and Windows x64 ride along
        once ARM 64 clears.
      </p>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────

export default function DeploymentPage() {
  return (
    <div>
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Deployment</h1>
        <p className="mt-1 text-sm text-gray-600">
          {ORG.name} — agent fleet health, agent version, Intune sync
        </p>
      </div>

      {/* Three stat tiles */}
      <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <OnlineTile />
        <VersionTile />
        <IntuneSyncTile />
      </div>

      {/* Platform + offline */}
      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <PlatformBreakdown />
        <OfflineAgents />
      </div>

      {/* Pilot prerequisite */}
      <div className="mt-6">
        <PilotPrerequisite />
      </div>

      {/* Cross-link footer */}
      <p className="mt-4 text-xs text-gray-500">
        Agent fleet metrics power the *Active users* tile on{' '}
        <Link
          href="/dashboard/overview"
          className="font-medium text-primary-600 hover:text-primary-700"
        >
          Overview
        </Link>{' '}
        — the numbers reconcile.
      </p>
    </div>
  );
}
