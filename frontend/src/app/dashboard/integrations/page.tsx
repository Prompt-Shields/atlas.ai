'use client';

// ─────────────────────────────────────────────────────────────────────
// Integrations — atlas dashboard
//
// Role:
//   The single surface where the IT lead connects Microsoft, Slack,
//   AWS, and GCP services. Mirrors the backend provider registry
//   (PR #31): every provider is rendered as a card whose state
//   tracks the live `IntegrationStatus` for the tenant.
//
// Sections:
//   1. Header with status summary (X connected, Y available, Z coming soon)
//   2. Category filter (All / Identity / Device / Data / Communication / Cloud)
//   3. Card grid (3 columns on lg, 2 on md, 1 on sm)
//   4. Each card: vendor logo + name + category + description +
//      capabilities + status chip + action button
//
// Actions per card status:
//   CONNECTED       → "Manage" button → detail drawer
//   NOT_CONNECTED   → "Connect" button → opens authorize URL
//   ERROR           → "Reconnect" button
//   DISABLED        → "Enable" button
//   COMING_SOON     → "Notify me" button (no-op for now)
//
// Microsoft providers all share the same Azure AD app — connecting
// Entra ID grants the base scopes; subsequent Microsoft providers
// just re-run consent for the additional scopes they need.
//
// Data source:
//   `INTEGRATION_CATALOGUE` from curated-demo-data.ts. Swap for
//   `await api.listIntegrations()` once that client method ships.
// ─────────────────────────────────────────────────────────────────────

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import {
  INTEGRATION_CATALOGUE,
  type IntegrationCard,
  type IntegrationCategory,
  type IntegrationStatus,
  type IntegrationVendor,
} from '@/lib/curated-demo-data';
import type {
  IntegrationCardResponse,
  IntegrationProvider,
} from '@/lib/types';

// ─── Constants ────────────────────────────────────────────────────────

const CATEGORY_LABELS: Record<IntegrationCategory | 'all', string> = {
  all: 'All',
  identity: 'Identity',
  device: 'Device',
  data: 'Data',
  communication: 'Communication',
  cloud: 'Cloud',
};

const CATEGORY_ORDER: (IntegrationCategory | 'all')[] = [
  'all',
  'identity',
  'device',
  'data',
  'communication',
  'cloud',
];

const STATUS_LABELS: Record<IntegrationStatus, string> = {
  CONNECTED: 'Connected',
  NOT_CONNECTED: 'Not connected',
  ERROR: 'Reconnect needed',
  DISABLED: 'Disabled',
  COMING_SOON: 'Coming soon',
};

const STATUS_CHIP_CLASSES: Record<IntegrationStatus, string> = {
  CONNECTED: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  NOT_CONNECTED: 'bg-gray-100 text-gray-700 ring-gray-200',
  ERROR: 'bg-red-50 text-red-700 ring-red-200',
  DISABLED: 'bg-amber-50 text-amber-700 ring-amber-200',
  COMING_SOON: 'bg-blue-50 text-blue-700 ring-blue-200',
};

const VENDOR_ACCENT: Record<IntegrationVendor, string> = {
  microsoft: 'bg-blue-50 text-blue-700 ring-blue-200',
  slack: 'bg-purple-50 text-purple-700 ring-purple-200',
  aws: 'bg-orange-50 text-orange-700 ring-orange-200',
  gcp: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  other: 'bg-gray-100 text-gray-700 ring-gray-200',
};

const VENDOR_INITIAL_BG: Record<IntegrationVendor, string> = {
  microsoft: 'bg-blue-600',
  slack: 'bg-purple-600',
  aws: 'bg-orange-600',
  gcp: 'bg-emerald-600',
  other: 'bg-gray-600',
};

// ─── Sub-components ───────────────────────────────────────────────────

function VendorLogo({ vendor, name }: { vendor: IntegrationVendor; name: string }) {
  // Real logos live at /logos/{slug}.svg in production. For the
  // curated demo we render a typographic mark so the page works
  // without binary assets.
  const initial = name.charAt(0);
  return (
    <div
      className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-base font-bold text-white ${VENDOR_INITIAL_BG[vendor]}`}
      aria-hidden="true"
    >
      {initial}
    </div>
  );
}

function StatusChip({ status }: { status: IntegrationStatus }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${STATUS_CHIP_CLASSES[status]}`}
    >
      {STATUS_LABELS[status]}
    </span>
  );
}

function CategoryFilter({
  selected,
  onChange,
  counts,
}: {
  selected: IntegrationCategory | 'all';
  onChange: (c: IntegrationCategory | 'all') => void;
  counts: Record<IntegrationCategory | 'all', number>;
}) {
  return (
    <div className="inline-flex flex-wrap gap-1 rounded-lg border border-gray-200 bg-white p-0.5 shadow-sm">
      {CATEGORY_ORDER.map((c) => {
        const active = c === selected;
        return (
          <button
            key={c}
            type="button"
            onClick={() => onChange(c)}
            className={
              active
                ? 'rounded-md bg-primary-600 px-3 py-1 text-xs font-semibold text-white'
                : 'rounded-md px-3 py-1 text-xs font-medium text-gray-600 hover:text-gray-900'
            }
          >
            {CATEGORY_LABELS[c]}
            <span
              className={
                'ml-1 text-[10px] font-medium ' +
                (active ? 'text-white/80' : 'text-gray-400')
              }
            >
              {counts[c]}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function buttonLabelForStatus(status: IntegrationStatus): string {
  switch (status) {
    case 'CONNECTED':
      return 'Manage';
    case 'ERROR':
      return 'Reconnect';
    case 'DISABLED':
      return 'Enable';
    case 'COMING_SOON':
      return 'Notify me';
    default:
      return 'Connect';
  }
}

function buttonStyleForStatus(status: IntegrationStatus): string {
  if (status === 'COMING_SOON') {
    return 'rounded-md border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-500';
  }
  if (status === 'CONNECTED') {
    return 'rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50';
  }
  if (status === 'ERROR') {
    return 'rounded-md bg-red-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-red-700';
  }
  return 'rounded-md bg-primary-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-primary-700';
}

function ProviderCard({
  card,
  onConnect,
  onManage,
}: {
  card: IntegrationCard;
  onConnect: (card: IntegrationCard) => void;
  onManage: (card: IntegrationCard) => void;
}) {
  const isClickable = card.status !== 'COMING_SOON';
  const onClick = () => {
    if (!isClickable) return;
    if (card.status === 'CONNECTED' || card.status === 'DISABLED') {
      onManage(card);
    } else {
      onConnect(card);
    }
  };

  return (
    <div className="flex h-full flex-col rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
      <div className="flex items-start gap-3">
        <VendorLogo
          vendor={card.meta.vendor}
          name={card.meta.shortName}
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <h3 className="truncate text-sm font-semibold text-gray-900">
              {card.meta.displayName}
            </h3>
            <StatusChip status={card.status} />
          </div>
          <div className="mt-1 flex items-center gap-2">
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ring-inset ${VENDOR_ACCENT[card.meta.vendor]}`}
            >
              {card.meta.shortName}
            </span>
            <span className="text-[11px] uppercase tracking-wide text-gray-500">
              {CATEGORY_LABELS[card.meta.category]}
            </span>
          </div>
        </div>
      </div>

      <p className="mt-3 text-xs text-gray-600">{card.meta.description}</p>

      <ul className="mt-3 space-y-1">
        {card.meta.capabilities.map((cap) => (
          <li
            key={cap}
            className="flex items-start gap-1.5 text-[11.5px] text-gray-700"
          >
            <span className="mt-0.5 text-emerald-500" aria-hidden="true">
              ✓
            </span>
            <span>{cap}</span>
          </li>
        ))}
      </ul>

      {card.status === 'CONNECTED' && (
        <div className="mt-3 rounded-md bg-gray-50 px-3 py-2 text-[11px] text-gray-600 ring-1 ring-gray-100">
          <p>
            <span className="font-semibold text-gray-900">
              {card.externalName || card.displayName}
            </span>
            {card.externalId && (
              <span className="ml-1 text-gray-500">· {card.externalId}</span>
            )}
          </p>
          {card.connectedAt && (
            <p className="mt-0.5 text-gray-500">
              Connected {new Date(card.connectedAt).toLocaleDateString()}
              {card.lastSyncedAt && (
                <>
                  {' '}· last sync{' '}
                  {new Date(card.lastSyncedAt).toLocaleString(undefined, {
                    month: 'short',
                    day: 'numeric',
                    hour: '2-digit',
                    minute: '2-digit',
                  })}
                </>
              )}
            </p>
          )}
        </div>
      )}

      {card.status === 'ERROR' && card.lastError && (
        <p className="mt-3 rounded-md bg-red-50 px-3 py-2 text-[11px] text-red-700 ring-1 ring-red-100">
          {card.lastError}
        </p>
      )}

      <div className="mt-4 flex items-center justify-end">
        <button
          type="button"
          onClick={onClick}
          disabled={!isClickable}
          className={
            buttonStyleForStatus(card.status) +
            (isClickable ? '' : ' cursor-not-allowed opacity-70')
          }
        >
          {buttonLabelForStatus(card.status)} →
        </button>
      </div>
    </div>
  );
}

// ─── Detail drawer ────────────────────────────────────────────────────

function DetailDrawer({
  card,
  onClose,
  onDisconnect,
}: {
  card: IntegrationCard | null;
  onClose: () => void;
  onDisconnect: (card: IntegrationCard) => void;
}) {
  if (!card) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-gray-900/40"
      role="dialog"
      aria-modal="true"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="h-full w-full max-w-md overflow-y-auto bg-white p-6 shadow-xl">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-start gap-3">
            <VendorLogo vendor={card.meta.vendor} name={card.meta.shortName} />
            <div>
              <h2 className="text-base font-semibold text-gray-900">
                {card.meta.displayName}
              </h2>
              <p className="mt-0.5 text-xs text-gray-500">
                {CATEGORY_LABELS[card.meta.category]} · {card.meta.vendor}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
          >
            ✕
          </button>
        </div>

        <div className="mt-5 space-y-4 text-sm">
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Status
            </h3>
            <p className="mt-1">
              <StatusChip status={card.status} />
            </p>
          </section>

          {card.externalName && (
            <section>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
                Connection
              </h3>
              <p className="mt-1 text-sm font-medium text-gray-900">
                {card.externalName}
              </p>
              {card.externalId && (
                <p className="mt-0.5 text-[11px] text-gray-500 font-mono">
                  {card.externalId}
                </p>
              )}
              {card.connectedAt && (
                <p className="mt-0.5 text-xs text-gray-600">
                  Connected{' '}
                  {new Date(card.connectedAt).toLocaleString(undefined, {
                    dateStyle: 'medium',
                    timeStyle: 'short',
                  })}
                </p>
              )}
            </section>
          )}

          {card.scopes.length > 0 && (
            <section>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
                Granted scopes
              </h3>
              <ul className="mt-2 flex flex-wrap gap-1">
                {card.scopes.map((s) => (
                  <li
                    key={s}
                    className="rounded-md bg-gray-50 px-2 py-0.5 text-[11px] font-mono text-gray-700 ring-1 ring-gray-100"
                  >
                    {s}
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Capabilities
            </h3>
            <ul className="mt-2 space-y-1">
              {card.meta.capabilities.map((cap) => (
                <li key={cap} className="text-xs text-gray-700">
                  • {cap}
                </li>
              ))}
            </ul>
          </section>
        </div>

        {card.status === 'CONNECTED' && (
          <div className="mt-6 space-y-3 border-t border-gray-100 pt-4">
            <Link
              href={`/dashboard/integrations/${card.meta.provider}`}
              className="block text-sm font-medium text-primary-600 hover:text-primary-700"
            >
              Configure {card.meta.shortName} →
            </Link>
            <button
              type="button"
              onClick={() => onDisconnect(card)}
              className="text-xs font-medium text-red-600 hover:text-red-700"
            >
              Disconnect {card.meta.shortName}
            </button>
            <p className="text-[11px] text-gray-500">
              Soft disconnect — clears tokens, preserves config. Re-connecting
              walks the OAuth flow again.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Toast ────────────────────────────────────────────────────────────

function Toast({
  message,
  onDone,
}: {
  message: string;
  onDone: () => void;
}) {
  useEffect(() => {
    const id = window.setTimeout(onDone, 3500);
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

// ─── Page ─────────────────────────────────────────────────────────────

// Convert the server's snake_case IntegrationCardResponse into the
// camelCase IntegrationCard the UI components expect. Keeps the
// existing rendering code unchanged when the live API drives the page.
function serverToCard(r: IntegrationCardResponse): IntegrationCard {
  return {
    meta: {
      provider: r.meta.provider,
      displayName: r.meta.display_name,
      shortName: r.meta.short_name,
      category: r.meta.category,
      vendor: r.meta.vendor,
      logoSlug: r.meta.logo_slug,
      description: r.meta.description,
      capabilities: r.meta.capabilities,
      available: r.meta.available,
      onboardingRecommended: r.meta.onboarding_recommended,
    },
    status: r.status as IntegrationStatus,
    integrationId: r.integration_id,
    displayName: r.display_name,
    externalId: r.external_id,
    externalName: r.external_name,
    scopes: r.scopes,
    lastSyncedAt: r.last_synced_at,
    lastError: r.last_error,
    connectedAt: r.connected_at,
  };
}

export default function IntegrationsPage() {
  const router = useRouter();
  const [category, setCategory] = useState<IntegrationCategory | 'all'>('all');
  const [detail, setDetail] = useState<IntegrationCard | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [localOverrides, setLocalOverrides] = useState<
    Record<string, IntegrationStatus>
  >({});
  // Live data from /api/v1/integrations; falls back to the curated
  // catalogue when the backend isn't reachable.
  const [serverCards, setServerCards] = useState<IntegrationCard[] | null>(
    null,
  );

  // Load live integrations on mount. Soft-fails to curated demo data.
  const refreshFromServer = async () => {
    try {
      const res = await api.listIntegrations();
      setServerCards(res.integrations.map(serverToCard));
    } catch {
      /* keep serverCards null → use curated fallback */
    }
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (cancelled) return;
      await refreshFromServer();
    })();
    return () => {
      cancelled = true;
    };
    // refreshFromServer is stable enough — no deps needed
  }, []);

  // Listen for the OAuth-callback postMessage. When the consent
  // window closes itself after a successful connect, it fires:
  //   { source: 'atlas-oauth', status: 'connected', provider, ... }
  // We refresh the integration list + toast.
  useEffect(() => {
    function onMessage(e: MessageEvent) {
      const data = e.data;
      if (!data || data.source !== 'atlas-oauth') return;
      if (data.status === 'connected') {
        setToast(
          `✓ ${data.display_name || data.provider} connected`,
        );
        void refreshFromServer();
      } else if (data.status === 'error') {
        setToast(
          `Couldn't connect ${data.provider}: ${data.error || 'unknown error'}`,
        );
      }
    }
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, []);

  const source = serverCards ?? INTEGRATION_CATALOGUE;
  const allCards = useMemo<IntegrationCard[]>(
    () =>
      source.map((c) => {
        const override = localOverrides[c.meta.provider];
        if (!override) return c;
        return { ...c, status: override };
      }),
    [source, localOverrides],
  );

  const filtered = useMemo(() => {
    if (category === 'all') return allCards;
    return allCards.filter((c) => c.meta.category === category);
  }, [category, allCards]);

  const counts = useMemo(() => {
    const c: Record<IntegrationCategory | 'all', number> = {
      all: allCards.length,
      identity: 0,
      device: 0,
      data: 0,
      communication: 0,
      cloud: 0,
    };
    for (const card of allCards) c[card.meta.category] += 1;
    return c;
  }, [allCards]);

  const summary = useMemo(() => {
    let connected = 0;
    let available = 0;
    let comingSoon = 0;
    for (const c of allCards) {
      if (c.status === 'CONNECTED') connected += 1;
      else if (c.status === 'COMING_SOON') comingSoon += 1;
      else available += 1;
    }
    return { connected, available, comingSoon };
  }, [allCards]);

  // Non-OAuth MDMs use the dedicated credential-form flow at
  // /dashboard/integrations/[provider]/connect (PR #49 backend
  // endpoints + this PR's UI). OAuth providers continue to use the
  // authorize-URL-in-new-tab path.
  const NON_OAUTH_MDMS = new Set([
    'JAMF_PRO',
    'KANDJI',
    'JUMPCLOUD',
  ]);

  const handleConnect = async (card: IntegrationCard) => {
    if (card.status === 'COMING_SOON') {
      setToast(`Got it — we'll email you when ${card.meta.shortName} ships.`);
      return;
    }

    // Sentinel gets its own connect wizard + data mapping + event
    // stream page rather than the generic OAuth / MDM-form flows.
    if (card.meta.provider === 'SENTINEL') {
      router.push('/dashboard/integrations/sentinel');
      return;
    }

    if (NON_OAUTH_MDMS.has(card.meta.provider)) {
      router.push(`/dashboard/integrations/${card.meta.provider}/connect`);
      return;
    }

    try {
      const start = await api.startIntegrationInstall(
        card.meta.provider as IntegrationProvider,
      );
      window.open(start.authorize_url, '_blank', 'noopener,noreferrer');
      setToast(
        `Opened ${card.meta.shortName} authorize page — finish the OAuth flow there.`,
      );
      // Don't flip status optimistically — the live page will reload
      // and pick up the new state once the OAuth callback persists.
    } catch {
      // No backend / not configured — local-only simulation so the
      // demo still flows.
      setLocalOverrides((prev) => ({
        ...prev,
        [card.meta.provider]: 'CONNECTED',
      }));
      setToast(`✓ ${card.meta.displayName} connected (demo)`);
    }
  };

  const handleManage = (card: IntegrationCard) => {
    if (card.meta.provider === 'SENTINEL') {
      router.push('/dashboard/integrations/sentinel');
      return;
    }
    setDetail(card);
  };

  const handleDisconnect = async (card: IntegrationCard) => {
    if (!confirm(`Disconnect ${card.meta.shortName}? Tokens will be cleared.`))
      return;

    if (card.integrationId) {
      try {
        await api.disconnectIntegration(card.integrationId);
        // Refresh server view to reflect the new state.
        const refreshed = await api.listIntegrations();
        setServerCards(refreshed.integrations.map(serverToCard));
        setDetail(null);
        setToast(`${card.meta.shortName} disconnected`);
        return;
      } catch {
        /* fall through to local simulation */
      }
    }
    setLocalOverrides((prev) => ({
      ...prev,
      [card.meta.provider]: 'NOT_CONNECTED',
    }));
    setDetail(null);
    setToast(`${card.meta.shortName} disconnected`);
  };

  return (
    <div>
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Integrations</h1>
          <p className="mt-1 text-sm text-gray-600">
            Connect Microsoft, Slack, AWS, and GCP services. Microsoft Entra ID
            is the recommended first connection.
          </p>
        </div>
        <div className="flex flex-wrap gap-3 text-xs">
          <span className="rounded-full bg-emerald-50 px-2.5 py-1 font-medium text-emerald-700 ring-1 ring-emerald-200">
            {summary.connected} connected
          </span>
          <span className="rounded-full bg-gray-100 px-2.5 py-1 font-medium text-gray-700 ring-1 ring-gray-200">
            {summary.available} available
          </span>
          <span className="rounded-full bg-blue-50 px-2.5 py-1 font-medium text-blue-700 ring-1 ring-blue-200">
            {summary.comingSoon} coming soon
          </span>
        </div>
      </div>

      {/* Category filter */}
      <div className="mt-6">
        <CategoryFilter
          selected={category}
          onChange={setCategory}
          counts={counts}
        />
      </div>

      {/* Grid */}
      <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        {filtered.map((card) => (
          <ProviderCard
            key={card.meta.provider}
            card={card}
            onConnect={handleConnect}
            onManage={handleManage}
          />
        ))}
      </div>

      {/* Footer cross-link */}
      <p className="mt-6 text-xs text-gray-500">
        New tenants are walked through the recommended connections during
        onboarding —{' '}
        <Link
          href="/onboarding"
          className="font-medium text-primary-600 hover:text-primary-700"
        >
          re-run the onboarding wizard
        </Link>
        . Need to export the AI component graph to an EA tool?{' '}
        <Link
          href="/dashboard/integrations/ardoq"
          className="font-medium text-primary-600 hover:text-primary-700"
        >
          Ardoq AI-Lens export
        </Link>
        .
      </p>

      <DetailDrawer
        card={detail}
        onClose={() => setDetail(null)}
        onDisconnect={handleDisconnect}
      />
      {toast && <Toast message={toast} onDone={() => setToast(null)} />}
    </div>
  );
}
