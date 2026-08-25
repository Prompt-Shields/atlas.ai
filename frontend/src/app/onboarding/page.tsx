'use client';

// ─────────────────────────────────────────────────────────────────────
// Onboarding wizard — first-time tenant setup.
//
// Six steps, in order:
//
//   1. Welcome              — tenant name, what atlas does, who this is for
//   2. Microsoft Entra ID   — recommended first connection (SSO + user sync)
//   3. Microsoft optionals  — Intune / Purview / Defender, all skippable
//   4. Slack                — recommended for the survey bot
//   5. Other clouds         — AWS / GCP teaser cards (Coming Soon)
//   6. Done                 — sets tenant.onboarding_completed_at,
//                             routes to /dashboard
//
// Each step has a Continue button + a Skip option (where applicable).
// Connections themselves go through the same OAuth URLs as the
// /dashboard/integrations grid — the wizard is sugar over the same
// `/api/v1/integrations/{provider}/install` endpoint.
//
// State machine:
//   - currentStep: 0..5
//   - skipped: Set<provider> — recorded for analytics + the recap on step 6
//   - connected: Set<provider> — mirrors what the user actioned during
//     the wizard. Live status (CONNECTED vs NOT_CONNECTED) comes from
//     the backend on next page load; this set is local-only.
//
// Data source:
//   `INTEGRATION_CATALOGUE` from curated-demo-data.ts. The wizard
//   only renders providers where `available=true` and
//   `onboardingRecommended=true` in steps 2 & 4; step 3 surfaces the
//   remaining Microsoft providers as optionals.
//
// On finish:
//   POST /api/v1/onboarding/complete (stubbed locally for now;
//   wire when api.ts gains the method). Then router.push('/dashboard').
// ─────────────────────────────────────────────────────────────────────

import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import {
  INTEGRATION_CATALOGUE,
  ORG,
  type IntegrationCard,
} from '@/lib/curated-demo-data';
import type { IntegrationProvider } from '@/lib/types';

// ─── Step definitions ────────────────────────────────────────────────

interface StepDef {
  key: string;
  title: string;
  subtitle: string;
}

const STEPS: StepDef[] = [
  {
    key: 'welcome',
    title: 'Welcome to atlas',
    subtitle:
      'A 60-second setup to connect your AI governance stack. You can come back to any of these later.',
  },
  {
    key: 'entra',
    title: 'Connect Microsoft Entra ID',
    subtitle:
      'The recommended first connection — Entra ID unlocks SSO, user / group / department sync, and is required for every other Microsoft integration.',
  },
  {
    key: 'ms-optionals',
    title: 'Additional Microsoft integrations',
    subtitle:
      'Optional, all skippable. Each adds depth to the dashboard — but you can connect them later from the Integrations tab.',
  },
  {
    key: 'slack',
    title: 'Connect Slack',
    subtitle:
      'Powers the survey-bot DM flow on the Registry page. Highly recommended if you want employees to self-register their AI use cases.',
  },
  {
    key: 'other-clouds',
    title: 'AWS & GCP',
    subtitle:
      "Cloud integrations for AWS Bedrock, IAM Identity Center, GCP Vertex, and Workspace are coming in v0.2. We'll notify you when each ships.",
  },
  {
    key: 'done',
    title: 'You’re all set',
    subtitle: 'Atlas is ready to start protecting your team’s AI use.',
  },
];

// ─── Helpers ─────────────────────────────────────────────────────────

function findProvider(provider: string): IntegrationCard | undefined {
  return INTEGRATION_CATALOGUE.find((c) => c.meta.provider === provider);
}

function VendorMark({
  vendor,
  letter,
  size = 'md',
}: {
  vendor: IntegrationCard['meta']['vendor'];
  letter: string;
  size?: 'md' | 'lg';
}) {
  const bg =
    vendor === 'microsoft'
      ? 'bg-blue-600'
      : vendor === 'slack'
        ? 'bg-purple-600'
        : vendor === 'aws'
          ? 'bg-orange-600'
          : vendor === 'gcp'
            ? 'bg-emerald-600'
            : 'bg-gray-600';
  const sz = size === 'lg' ? 'h-12 w-12 text-lg' : 'h-10 w-10 text-base';
  return (
    <div
      className={`flex shrink-0 items-center justify-center rounded-lg font-bold text-white ${bg} ${sz}`}
      aria-hidden="true"
    >
      {letter}
    </div>
  );
}

// ─── Step progress ───────────────────────────────────────────────────

function StepDots({ current }: { current: number }) {
  return (
    <ol className="flex items-center gap-2">
      {STEPS.map((s, i) => (
        <li
          key={s.key}
          className={
            'h-1.5 flex-1 rounded-full ' +
            (i < current
              ? 'bg-primary-600'
              : i === current
                ? 'bg-primary-300'
                : 'bg-gray-200')
          }
          aria-current={i === current ? 'step' : undefined}
        />
      ))}
    </ol>
  );
}

// ─── Step screens ────────────────────────────────────────────────────

function WelcomeStep({ onContinue }: { onContinue: () => void }) {
  return (
    <div>
      <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
        <p className="text-sm text-gray-700">
          Hi <span className="font-semibold text-gray-900">{ORG.name}</span> — atlas helps your IT lead see and shape how the
          team uses AI: who is using what, where sensitive data lands,
          and which policies are in force.
        </p>
        <ul className="mt-4 space-y-2 text-sm text-gray-700">
          <li className="flex items-start gap-2">
            <span className="mt-0.5 text-emerald-500">✓</span>
            <span>
              Microsoft Entra ID for SSO and user sync (recommended first)
            </span>
          </li>
          <li className="flex items-start gap-2">
            <span className="mt-0.5 text-emerald-500">✓</span>
            <span>Slack for the AI use-case registration survey bot</span>
          </li>
          <li className="flex items-start gap-2">
            <span className="mt-0.5 text-emerald-500">✓</span>
            <span>
              Optional: Intune, Purview, Defender for Cloud Apps
            </span>
          </li>
        </ul>
        <p className="mt-4 rounded-md bg-blue-50 px-3 py-2 text-xs text-blue-900 ring-1 ring-blue-100">
          You can skip any step and come back later from{' '}
          <strong>Integrations</strong> in the sidebar.
        </p>
      </div>
      <div className="mt-4 flex justify-end">
        <button
          type="button"
          onClick={onContinue}
          className="rounded-md bg-primary-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-primary-700"
        >
          Start setup →
        </button>
      </div>
    </div>
  );
}

function ConnectStep({
  card,
  isConnected,
  onConnect,
  onSkip,
  onContinue,
  primary = true,
}: {
  card: IntegrationCard;
  isConnected: boolean;
  onConnect: () => void;
  onSkip: () => void;
  onContinue: () => void;
  primary?: boolean;
}) {
  return (
    <div>
      <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
        <div className="flex items-start gap-4">
          <VendorMark
            vendor={card.meta.vendor}
            letter={card.meta.shortName.charAt(0)}
            size="lg"
          />
          <div className="min-w-0 flex-1">
            <h3 className="text-base font-semibold text-gray-900">
              {card.meta.displayName}
            </h3>
            <p className="mt-1 text-sm text-gray-600">
              {card.meta.description}
            </p>
          </div>
          {isConnected && (
            <span className="inline-flex items-center rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700 ring-1 ring-emerald-200">
              ✓ Connected
            </span>
          )}
        </div>

        <ul className="mt-4 space-y-1.5">
          {card.meta.capabilities.map((cap) => (
            <li
              key={cap}
              className="flex items-start gap-2 text-sm text-gray-700"
            >
              <span className="mt-0.5 text-emerald-500" aria-hidden="true">
                ✓
              </span>
              <span>{cap}</span>
            </li>
          ))}
        </ul>

        {card.meta.vendor === 'microsoft' && (
          <p className="mt-4 rounded-md bg-blue-50 px-3 py-2 text-[11px] text-blue-900 ring-1 ring-blue-100">
            You&apos;ll be redirected to Microsoft to grant consent for the
            requested scopes. Atlas stores only an encrypted refresh
            token; we never see your credentials.
          </p>
        )}

        <div className="mt-5 flex flex-wrap items-center justify-end gap-2">
          <button
            type="button"
            onClick={onSkip}
            className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            Skip for now
          </button>
          {isConnected ? (
            <button
              type="button"
              onClick={onContinue}
              className="rounded-md bg-primary-600 px-4 py-2 text-sm font-semibold text-white hover:bg-primary-700"
            >
              Continue →
            </button>
          ) : (
            <button
              type="button"
              onClick={onConnect}
              className={
                primary
                  ? 'rounded-md bg-primary-600 px-4 py-2 text-sm font-semibold text-white hover:bg-primary-700'
                  : 'rounded-md bg-gray-900 px-4 py-2 text-sm font-semibold text-white hover:bg-gray-800'
              }
            >
              Connect {card.meta.shortName} →
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function MicrosoftOptionalsStep({
  onContinue,
  onSkipAll,
  connected,
  onConnectOne,
}: {
  onContinue: () => void;
  onSkipAll: () => void;
  connected: Set<string>;
  onConnectOne: (provider: string) => void;
}) {
  const optionals = useMemo(
    () =>
      INTEGRATION_CATALOGUE.filter(
        (c) =>
          c.meta.vendor === 'microsoft' &&
          !c.meta.onboardingRecommended &&
          c.meta.available,
      ),
    [],
  );

  return (
    <div>
      <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
        <ul className="space-y-3">
          {optionals.map((card) => {
            const isConnected = connected.has(card.meta.provider);
            return (
              <li
                key={card.meta.provider}
                className="flex items-start gap-3 rounded-lg border border-gray-200 p-3"
              >
                <VendorMark
                  vendor={card.meta.vendor}
                  letter={card.meta.shortName.charAt(0)}
                />
                <div className="min-w-0 flex-1">
                  <h3 className="text-sm font-semibold text-gray-900">
                    {card.meta.displayName}
                  </h3>
                  <p className="mt-0.5 text-xs text-gray-600">
                    {card.meta.description}
                  </p>
                </div>
                {isConnected ? (
                  <span className="inline-flex items-center rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700 ring-1 ring-emerald-200">
                    Connected
                  </span>
                ) : (
                  <button
                    type="button"
                    onClick={() => onConnectOne(card.meta.provider)}
                    className="rounded-md border border-gray-300 bg-white px-3 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50"
                  >
                    Connect
                  </button>
                )}
              </li>
            );
          })}
        </ul>
        <p className="mt-3 text-[11px] text-gray-500">
          You can connect any of these later from the{' '}
          <strong>Integrations</strong> tab.
        </p>
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <button
          type="button"
          onClick={onSkipAll}
          className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
        >
          Skip all
        </button>
        <button
          type="button"
          onClick={onContinue}
          className="rounded-md bg-primary-600 px-4 py-2 text-sm font-semibold text-white hover:bg-primary-700"
        >
          Continue →
        </button>
      </div>
    </div>
  );
}

function OtherCloudsStep({ onContinue }: { onContinue: () => void }) {
  const others = useMemo(
    () =>
      INTEGRATION_CATALOGUE.filter(
        (c) => c.meta.vendor === 'aws' || c.meta.vendor === 'gcp',
      ),
    [],
  );

  return (
    <div>
      <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {others.map((card) => (
            <div
              key={card.meta.provider}
              className="flex items-start gap-3 rounded-lg border border-dashed border-gray-300 bg-gray-50 p-3"
            >
              <VendorMark
                vendor={card.meta.vendor}
                letter={card.meta.shortName.charAt(0)}
              />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold text-gray-900">
                  {card.meta.displayName}
                </p>
                <p className="mt-0.5 text-xs text-gray-500">
                  {card.meta.description}
                </p>
                <span className="mt-1.5 inline-block rounded-full bg-blue-50 px-2 py-0.5 text-[10px] font-medium text-blue-700 ring-1 ring-blue-200">
                  Coming Soon
                </span>
              </div>
            </div>
          ))}
        </div>
        <p className="mt-3 text-[11px] text-gray-500">
          We&apos;ll email you when each lands. No action required here.
        </p>
      </div>
      <div className="mt-4 flex justify-end">
        <button
          type="button"
          onClick={onContinue}
          className="rounded-md bg-primary-600 px-4 py-2 text-sm font-semibold text-white hover:bg-primary-700"
        >
          Continue →
        </button>
      </div>
    </div>
  );
}

function DoneStep({
  onFinish,
  connected,
  skipped,
}: {
  onFinish: () => void;
  connected: Set<string>;
  skipped: Set<string>;
}) {
  const connectedList = useMemo(
    () =>
      INTEGRATION_CATALOGUE.filter((c) => connected.has(c.meta.provider)),
    [connected],
  );
  const skippedList = useMemo(
    () =>
      INTEGRATION_CATALOGUE.filter(
        (c) => skipped.has(c.meta.provider) && c.meta.available,
      ),
    [skipped],
  );

  return (
    <div>
      <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
        <p className="text-sm text-gray-700">
          Atlas is ready. Here&apos;s what you set up:
        </p>

        {connectedList.length > 0 && (
          <section className="mt-4">
            <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Connected
            </h4>
            <ul className="mt-2 space-y-1.5">
              {connectedList.map((c) => (
                <li
                  key={c.meta.provider}
                  className="flex items-center gap-2 text-sm text-gray-700"
                >
                  <span className="text-emerald-500">✓</span>
                  <span>{c.meta.displayName}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        {skippedList.length > 0 && (
          <section className="mt-4">
            <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Skipped for now
            </h4>
            <ul className="mt-2 space-y-1.5">
              {skippedList.map((c) => (
                <li
                  key={c.meta.provider}
                  className="flex items-center gap-2 text-sm text-gray-600"
                >
                  <span className="text-gray-400">○</span>
                  <span>{c.meta.displayName}</span>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-[11px] text-gray-500">
              Re-visit any of these from{' '}
              <Link
                href="/dashboard/integrations"
                className="font-medium text-primary-600 hover:text-primary-700"
              >
                Integrations
              </Link>
              .
            </p>
          </section>
        )}

        <p className="mt-5 rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-900 ring-1 ring-emerald-100">
          <strong>What&apos;s next.</strong> Visit{' '}
          <strong>Overview</strong> for a real-time view of AI use across
          your org; <strong>Registry</strong> to send the first survey;
          and <strong>Policies</strong> to push your first redact /
          coach / block rule.
        </p>
      </div>
      <div className="mt-4 flex justify-end">
        <button
          type="button"
          onClick={onFinish}
          className="rounded-md bg-primary-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-primary-700"
        >
          Go to dashboard →
        </button>
      </div>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────

export default function OnboardingPage() {
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [connected, setConnected] = useState<Set<string>>(new Set());
  const [skipped, setSkipped] = useState<Set<string>>(new Set());

  const entra = findProvider('MICROSOFT_ENTRA_ID');
  const slack = findProvider('SLACK');

  // If the tenant has already completed onboarding, bounce to /dashboard.
  // Defensive: if the user lands here via a direct link.
  useEffect(() => {
    let cancelled = false;
    api
      .getOnboardingStatus()
      .then((status) => {
        if (!cancelled && status.is_complete) {
          router.replace('/dashboard');
        }
      })
      .catch(() => {
        /* soft fail — let user proceed */
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  // Listen for the OAuth-callback postMessage from the consent
  // popup. When it fires, mark the provider as connected so the
  // step's CTA flips from "Connect" to "✓ Connected" without
  // needing the user to refresh.
  useEffect(() => {
    function onMessage(e: MessageEvent) {
      const data = e.data;
      if (!data || data.source !== 'atlas-oauth') return;
      if (data.status === 'connected' && typeof data.provider === 'string') {
        setConnected((prev) => {
          const next = new Set(prev);
          next.add(data.provider);
          return next;
        });
        setSkipped((prev) => {
          const next = new Set(prev);
          next.delete(data.provider);
          return next;
        });
      }
    }
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, []);

  const markConnected = (provider: string) => {
    setConnected((prev) => {
      const next = new Set(prev);
      next.add(provider);
      return next;
    });
    setSkipped((prev) => {
      const next = new Set(prev);
      next.delete(provider);
      return next;
    });
  };

  const markSkipped = (provider: string) => {
    setSkipped((prev) => {
      const next = new Set(prev);
      next.add(provider);
      return next;
    });
  };

  /**
   * Connect handler used by every ConnectStep + the MicrosoftOptionals
   * row Connect buttons. Tries the live backend install endpoint
   * first; on success opens the authorize URL in a new tab + marks
   * the provider as connected optimistically. On failure (no backend
   * during the demo) falls back to the local simulation so the wizard
   * still flows end-to-end.
   */
  const handleConnect = async (provider: string): Promise<void> => {
    try {
      const start = await api.startIntegrationInstall(
        provider as IntegrationProvider,
      );
      window.open(start.authorize_url, '_blank', 'noopener,noreferrer');
      // The actual flip to CONNECTED happens after the OAuth
      // callback persists the Integration row. The wizard marks
      // the step as "started" so the user can move on.
      markConnected(provider);
    } catch {
      // No backend available — demo mode. Simulate.
      markConnected(provider);
    }
  };

  const finish = async () => {
    try {
      await api.completeOnboarding(Array.from(connected));
    } catch {
      /* soft fail — still route, the timestamp will retry on next mount */
    }
    router.push('/dashboard');
  };

  const goNext = () => setStep((s) => Math.min(s + 1, STEPS.length - 1));
  const goPrev = () => setStep((s) => Math.max(s - 1, 0));

  const def = STEPS[step];

  return (
    <div>
      {/* Header */}
      <div>
        <p className="text-xs font-semibold uppercase tracking-wide text-primary-700">
          Step {step + 1} of {STEPS.length}
        </p>
        <h1 className="mt-1 text-2xl font-bold text-gray-900">{def.title}</h1>
        <p className="mt-1 text-sm text-gray-600">{def.subtitle}</p>
      </div>

      {/* Progress dots */}
      <div className="mt-4">
        <StepDots current={step} />
      </div>

      {/* Step content */}
      <div className="mt-6">
        {step === 0 && <WelcomeStep onContinue={goNext} />}

        {step === 1 && entra && (
          <ConnectStep
            card={entra}
            isConnected={connected.has('MICROSOFT_ENTRA_ID')}
            onConnect={() => {
              void handleConnect('MICROSOFT_ENTRA_ID');
            }}
            onSkip={() => {
              markSkipped('MICROSOFT_ENTRA_ID');
              goNext();
            }}
            onContinue={goNext}
            primary
          />
        )}

        {step === 2 && (
          <MicrosoftOptionalsStep
            connected={connected}
            onConnectOne={(p) => {
              void handleConnect(p);
            }}
            onContinue={goNext}
            onSkipAll={() => {
              ['MICROSOFT_INTUNE', 'MICROSOFT_PURVIEW', 'MICROSOFT_DEFENDER_CASB'].forEach(
                (p) => {
                  if (!connected.has(p)) markSkipped(p);
                },
              );
              goNext();
            }}
          />
        )}

        {step === 3 && slack && (
          <ConnectStep
            card={slack}
            isConnected={connected.has('SLACK')}
            onConnect={() => {
              void handleConnect('SLACK');
            }}
            onSkip={() => {
              markSkipped('SLACK');
              goNext();
            }}
            onContinue={goNext}
          />
        )}

        {step === 4 && <OtherCloudsStep onContinue={goNext} />}

        {step === 5 && (
          <DoneStep
            onFinish={finish}
            connected={connected}
            skipped={skipped}
          />
        )}
      </div>

      {/* Bottom controls */}
      {step > 0 && step < STEPS.length - 1 && (
        <div className="mt-4">
          <button
            type="button"
            onClick={goPrev}
            className="text-xs font-medium text-gray-500 hover:text-gray-700"
          >
            ← Back
          </button>
        </div>
      )}
    </div>
  );
}
