'use client';

// ─────────────────────────────────────────────────────────────────────
// Discover — Shadow AI (atlas dashboard §3.6)
//
// Surfaces unsanctioned AI tools that Defender for Cloud Apps + the
// Promptly extension have detected in use. Each detection is a (app,
// users) tuple — clicking *Promote* opens the /register form
// pre-filled with the app's name and the detected user count.
//
// Data: SHADOW_APP_DETECTIONS from curated-demo-data.ts. When the
// Defender CASB worker (PR #39) populates CloudAppDetection rows for
// real, swap to api.listShadowAppDetections() — JSX unchanged.
// ─────────────────────────────────────────────────────────────────────

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { Satellite, Link2, Server, Mic, ShieldAlert } from 'lucide-react';
import {
  ORG,
  SHADOW_APP_DETECTIONS,
  type ShadowAppDetection,
} from '@/lib/curated-demo-data';
import { api } from '@/lib/api';
import { AGENT_CONVERSATIONS, DISCOVERY_STATS } from '@/lib/aispm/discovery';
import { AgentConversationCard } from '@/components/spm/agent-conversation-card';
import {
  LaunchCampaignModal,
  type CampaignDispatch,
} from '@/components/spm/launch-campaign-modal';

const STATUS_CLASSES: Record<ShadowAppDetection['status'], string> = {
  Detected: 'bg-amber-50 text-amber-700 ring-amber-200',
  'Under review': 'bg-blue-50 text-blue-700 ring-blue-200',
  Approved: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  Blocked: 'bg-red-50 text-red-700 ring-red-200',
};

function StatusChip({ status }: { status: ShadowAppDetection['status'] }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${STATUS_CLASSES[status]}`}
    >
      {status}
    </span>
  );
}

function RiskScore({ score }: { score: number }) {
  const colour =
    score >= 7
      ? 'bg-red-500'
      : score >= 5
        ? 'bg-amber-500'
        : 'bg-emerald-500';
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 w-20 rounded-full bg-gray-100">
        <div
          className={`h-2 rounded-full ${colour}`}
          style={{ width: `${score * 10}%` }}
        />
      </div>
      <span className="font-medium text-gray-900 tabular-nums">{score}/10</span>
    </div>
  );
}

// ─── Sample-data badge ────────────────────────────────────────────────

function SampleBadge() {
  return (
    <span className="inline-flex items-center rounded-full border border-gray-200 bg-gray-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-gray-500">
      Sample data
    </span>
  );
}

// ─── Toast ────────────────────────────────────────────────────────────

function Toast({ message, onDone }: { message: string; onDone: () => void }) {
  useEffect(() => {
    const id = window.setTimeout(onDone, 4500);
    return () => window.clearTimeout(id);
  }, [onDone]);
  return (
    <div
      className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-lg bg-gray-900 px-4 py-2 text-sm text-white shadow-xl"
      role="status"
    >
      {message}
    </div>
  );
}

export default function DiscoverPage() {
  const [statusFilter, setStatusFilter] = useState<
    ShadowAppDetection['status'] | 'all'
  >('all');

  // AI Discovery section state
  const [campaignLaunched, setCampaignLaunched] = useState(false);
  const [launchModalOpen, setLaunchModalOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  /**
   * Launch Campaign handler — dispatches the REAL `foundation-use-case`
   * survey via the survey engine. Seeds the template first (idempotent),
   * then POSTs the dispatch. On any failure we soft-fail to the demo
   * animation so the flow never throws to the user.
   */
  const handleLaunch = async (d: CampaignDispatch) => {
    setLaunchModalOpen(false);
    setCampaignLaunched(true);
    const channelLabel = d.channel === 'slack' ? 'Slack' : 'Email';

    try {
      try {
        await api.seedDefaultSurveyTemplate();
      } catch {
        /* if seeding fails the dispatch will throw with a clearer error */
      }

      const created = await api.dispatchSurvey({
        template_slug: 'foundation-use-case',
        template_version: 'v1',
        audience: d.audience,
        channel: d.channel === 'email' ? 'EMAIL' : 'SLACK',
      });

      setToast(
        `✓ Campaign launched — survey dispatched to ${created.recipient_count} recipients via ${channelLabel}`,
      );
    } catch (err) {
      const message =
        err instanceof Error ? err.message : 'no Slack workspace connected';
      setToast(`Campaign queued (demo) — ${message}`);
    }
  };

  const handleCopyLink = async () => {
    const link = `${window.location.origin}/dashboard/register`;
    try {
      await navigator.clipboard.writeText(link);
      setToast('✓ Link copied');
    } catch {
      setToast(`Copy this link: ${link}`);
    }
  };

  const filtered = useMemo(
    () =>
      statusFilter === 'all'
        ? SHADOW_APP_DETECTIONS
        : SHADOW_APP_DETECTIONS.filter((d) => d.status === statusFilter),
    [statusFilter],
  );

  const counts = useMemo(() => {
    const c = {
      total: SHADOW_APP_DETECTIONS.length,
      critical: 0,
      unauthorized: 0,
    };
    for (const d of SHADOW_APP_DETECTIONS) {
      if (d.riskScore >= 7) c.critical += 1;
      if (d.status === 'Detected' || d.status === 'Under review') {
        c.unauthorized += d.detectedUsers;
      }
    }
    return c;
  }, []);

  const visibilityScore = Math.max(
    0,
    100 -
      SHADOW_APP_DETECTIONS.filter((d) => d.status === 'Detected').length * 8,
  );

  return (
    <div>
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Discover</h1>
        <p className="mt-1 text-sm text-gray-600">
          {ORG.name} — Shadow AI tools detected by Promptly + Defender for
          Cloud Apps over the last 30 days
        </p>
      </div>

      {/* ─── AI Discovery ───────────────────────────────────────────── */}
      <section className="mt-8">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-semibold text-gray-900">AI Discovery</h2>
          <SampleBadge />
        </div>
        <p className="mt-1 text-sm text-gray-600">
          Agentic interviews + employee self-registration to map AI use across
          every department.
        </p>

        {/* Entry cards */}
        <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
            <div className="mb-3 flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600">
              <Satellite size={18} />
            </div>
            <h3 className="text-sm font-semibold text-gray-900">
              AI Discovery Campaign
            </h3>
            <p className="mt-1 text-xs leading-relaxed text-gray-500">
              Send AI agents to interview every department. Agents ask about AI
              tool usage, data handling, and risk exposure.
            </p>
            <button
              type="button"
              onClick={() => setLaunchModalOpen(true)}
              className="mt-4 rounded-md bg-primary-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-primary-700"
            >
              Launch Campaign
            </button>
          </div>

          <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
            <div className="mb-3 flex h-9 w-9 items-center justify-center rounded-lg bg-sky-50 text-sky-600">
              <Link2 size={18} />
            </div>
            <h3 className="text-sm font-semibold text-gray-900">
              Employee Self-Registration
            </h3>
            <p className="mt-1 text-xs leading-relaxed text-gray-500">
              Share a link with staff. An AI agent interviews them
              conversationally and extracts use case data automatically.
            </p>
            <button
              type="button"
              onClick={handleCopyLink}
              className="mt-4 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-semibold text-gray-700 hover:bg-gray-50"
            >
              Copy Link
            </button>
          </div>

          <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
            <div className="mb-3 flex h-9 w-9 items-center justify-center rounded-lg bg-violet-50 text-violet-600">
              <Server size={18} />
            </div>
            <h3 className="text-sm font-semibold text-gray-900">
              MCP Server Discovery
            </h3>
            <p className="mt-1 text-xs leading-relaxed text-gray-500">
              Correlate IDE configs, network traffic, browser telemetry, and
              process lists into scored MCP server candidates.
            </p>
            <Link
              href="/dashboard/discover/mcp"
              className="mt-4 inline-block rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-semibold text-gray-700 hover:bg-gray-50"
            >
              View candidates →
            </Link>
          </div>

          <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
            <div className="mb-3 flex h-9 w-9 items-center justify-center rounded-lg bg-emerald-50 text-emerald-600">
              <Mic size={18} />
            </div>
            <h3 className="text-sm font-semibold text-gray-900">
              Voice-interview Intake
            </h3>
            <p className="mt-1 text-xs leading-relaxed text-gray-500">
              An AI agent voice-interviews an employee; the transcript is
              parsed into documented use-case drafts.
            </p>
            <Link
              href="/dashboard/discover/voice-interview"
              className="mt-4 inline-block rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-semibold text-gray-700 hover:bg-gray-50"
            >
              Play transcript →
            </Link>
          </div>

          <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
            <div className="mb-3 flex h-9 w-9 items-center justify-center rounded-lg bg-rose-50 text-rose-600">
              <ShieldAlert size={18} />
            </div>
            <h3 className="text-sm font-semibold text-gray-900">
              Defender for Cloud Apps Import
            </h3>
            <p className="mt-1 text-xs leading-relaxed text-gray-500">
              Paste a Defender for Cloud Apps export to extract and enrich
              discovered apps with risk and compliance data.
            </p>
            <Link
              href="/dashboard/discover/defender-import"
              className="mt-4 inline-block rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-semibold text-gray-700 hover:bg-gray-50"
            >
              Import apps →
            </Link>
          </div>
        </div>

        {/* Stat tiles */}
        <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[
            {
              label: 'AI Tools Discovered',
              value: DISCOVERY_STATS.toolsDiscovered,
              color: 'text-gray-900',
            },
            {
              label: 'Vendors in Use',
              value: DISCOVERY_STATS.vendorsInUse,
              color: 'text-sky-600',
            },
            {
              label: 'Use Cases Identified',
              value: DISCOVERY_STATS.useCasesFound,
              color: 'text-indigo-600',
            },
            {
              label: 'Shadow AI Detected',
              value: DISCOVERY_STATS.shadowAiFound,
              color: 'text-red-500',
            },
          ].map((s) => (
            <div
              key={s.label}
              className="rounded-xl bg-white p-4 text-center shadow-sm ring-1 ring-gray-200"
            >
              <div className={`text-2xl font-bold ${s.color}`}>{s.value}</div>
              <div className="mt-1 text-xs text-gray-400">{s.label}</div>
            </div>
          ))}
        </div>

        {/* Agent conversations */}
        <div className="mt-6">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-gray-700">
              Agent Conversations
            </h3>
            <SampleBadge />
          </div>
          <div className="mt-3 grid gap-4 sm:grid-cols-2">
            {AGENT_CONVERSATIONS.map((conv, i) => (
              <AgentConversationCard
                key={conv.id}
                conversation={conv}
                autoPlay={campaignLaunched}
                delay={i * 800}
              />
            ))}
          </div>
        </div>
      </section>

      {/* ─── Shadow AI detection (existing) ─────────────────────────── */}
      <div className="mt-10 border-t border-gray-200 pt-8">
        <h2 className="text-lg font-semibold text-gray-900">
          Shadow AI detections
        </h2>
        <p className="mt-1 text-sm text-gray-600">
          Unsanctioned AI tools detected by Promptly + Defender for Cloud Apps.
        </p>
      </div>

      {/* Hero tiles */}
      <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Detected services
          </p>
          <p className="mt-2 text-3xl font-bold text-gray-900">
            {counts.total}
          </p>
        </div>
        <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Critical risk
          </p>
          <p className="mt-2 text-3xl font-bold text-red-600">
            {counts.critical}
          </p>
        </div>
        <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Unauthorized users
          </p>
          <p className="mt-2 text-3xl font-bold text-amber-600">
            {counts.unauthorized}
          </p>
        </div>
        <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Visibility score
          </p>
          <p className="mt-2 text-3xl font-bold text-emerald-600">
            {visibilityScore}
            <span className="text-base font-medium text-gray-500">/100</span>
          </p>
        </div>
      </div>

      {/* Status filter */}
      <div className="mt-6 inline-flex rounded-lg border border-gray-200 bg-white p-0.5 shadow-sm">
        {(['all', 'Detected', 'Under review', 'Approved', 'Blocked'] as const).map(
          (s) => {
            const active = statusFilter === s;
            return (
              <button
                key={s}
                type="button"
                onClick={() => setStatusFilter(s)}
                className={
                  active
                    ? 'rounded-md bg-primary-600 px-3 py-1 text-xs font-semibold text-white'
                    : 'rounded-md px-3 py-1 text-xs font-medium text-gray-600 hover:text-gray-900'
                }
              >
                {s === 'all' ? 'All' : s}
              </button>
            );
          },
        )}
      </div>

      {/* Detection table */}
      <div className="mt-6 overflow-hidden rounded-xl bg-white shadow-sm ring-1 ring-gray-200">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-100 text-sm">
            <thead>
              <tr className="bg-gray-50 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
                <th className="px-4 py-2">App</th>
                <th className="px-4 py-2">Category</th>
                <th className="px-4 py-2">Risk score</th>
                <th className="px-4 py-2">Users</th>
                <th className="px-4 py-2">First seen</th>
                <th className="px-4 py-2">Last seen</th>
                <th className="px-4 py-2">Status</th>
                <th className="px-4 py-2"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {filtered.map((d) => (
                <tr key={d.id} className="hover:bg-gray-50">
                  <td className="px-4 py-3">
                    <div className="font-medium text-gray-900">
                      {d.appName}
                    </div>
                    <div className="text-xs text-gray-500">{d.appDomain}</div>
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-600">
                    {d.appCategory}
                  </td>
                  <td className="px-4 py-3">
                    <RiskScore score={d.riskScore} />
                  </td>
                  <td className="px-4 py-3 text-gray-700 tabular-nums">
                    {d.detectedUsers}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-xs text-gray-500">
                    {d.firstSeen}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-xs text-gray-500">
                    {d.lastSeen}
                  </td>
                  <td className="px-4 py-3">
                    <StatusChip status={d.status} />
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Link
                      href={`/dashboard/register?app=${encodeURIComponent(d.appName)}&detected_users=${d.detectedUsers}`}
                      className="text-xs font-medium text-primary-600 hover:text-primary-700"
                    >
                      Promote →
                    </Link>
                  </td>
                </tr>
              ))}
              {filtered.length === 0 && (
                <tr>
                  <td
                    colSpan={8}
                    className="px-4 py-8 text-center text-sm text-gray-500"
                  >
                    No detections match this filter.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <p className="mt-4 text-xs text-gray-500">
        Detections come from{' '}
        <Link
          href="/dashboard/integrations/MICROSOFT_DEFENDER_CASB"
          className="font-medium text-primary-600 hover:text-primary-700"
        >
          Microsoft Defender for Cloud Apps
        </Link>{' '}
        and the Promptly browser extension.
      </p>

      <LaunchCampaignModal
        open={launchModalOpen}
        onClose={() => setLaunchModalOpen(false)}
        onDispatch={handleLaunch}
      />
      {toast && <Toast message={toast} onDone={() => setToast(null)} />}
    </div>
  );
}
