'use client';

// ─────────────────────────────────────────────────────────────────────
// Public pricing page — atlas.ai
//
// Mirrors the tier model from docs/pricing-and-billing.md (PR #20).
// Four tiers + FAQ + Stripe-ready CTAs. The /login flow handles
// Free/Pro signups today; Team and Enterprise route to a contact-
// sales mailto until the Stripe Checkout wiring lands (post the
// pricing-doc launch plan §5).
// ─────────────────────────────────────────────────────────────────────

import Link from 'next/link';

interface Tier {
  name: string;
  audience: string;
  monthly: string;
  monthlyDetail: string;
  trial: string;
  cta: { label: string; href: string };
  emphasis?: boolean;
  features: string[];
}

const TIERS: Tier[] = [
  {
    name: 'Free',
    audience: 'Solo, in-browser',
    monthly: '$0',
    monthlyDetail: 'forever',
    trial: 'No credit card',
    cta: { label: 'Get the extension', href: '/login' },
    features: [
      'Browser extension (Chrome + Safari)',
      'PII detection — all 11 categories, local',
      '5 default roles + unlimited custom roles',
      'Cross-LLM context portability via Markdown',
      'Community Discord support',
    ],
  },
  {
    name: 'Pro',
    audience: 'Cross-device, cross-LLM',
    monthly: '$10',
    monthlyDetail: 'per user / month, or $96/year (20% off)',
    trial: 'No trial — Free does the job',
    cta: { label: 'Start free, upgrade later', href: '/login' },
    features: [
      'Everything in Free',
      'Role library synced across devices',
      'Custom role sharing via short links',
      'Personal usage dashboard',
      'Email support, 2-business-day response',
    ],
  },
  {
    name: 'Team',
    audience: 'Governance for the IT lead',
    monthly: '$29',
    monthlyDetail: 'per user / month · 5-seat minimum',
    trial: '14-day free trial',
    cta: { label: 'Start 14-day trial', href: '/login' },
    emphasis: true,
    features: [
      'Everything in Pro',
      'Atlas dashboard — Overview / Activity Log / Coaching / Policies / Audit',
      'LLM-powered bias detection + policy semantic matching',
      'Microsoft Entra ID / Intune / Purview / Defender for Cloud Apps integrations',
      'Slack survey bot for use-case registration',
      'Priority email + shared Slack Connect channel',
    ],
  },
  {
    name: 'Enterprise',
    audience: 'Regulated industries · sales-led',
    monthly: 'Custom',
    monthlyDetail: '100-seat minimum · paid 60-day pilot',
    trial: 'Pilot credits roll into annual',
    cta: { label: 'Contact sales', href: 'mailto:hello@atlas.example?subject=Atlas Enterprise' },
    features: [
      'Everything in Team',
      'SSO (SAML 2.0 / OIDC) — Auth0 or Entra ID direct',
      'SCIM provisioning',
      'Audit log export to Sentinel / Splunk / Chronicle',
      'On-prem / VPC-isolated deployment option',
      'SOC 2 Type II + ISO 27001 attestation',
      'Dedicated CSM · quarterly business review',
    ],
  },
];

const FAQ: Array<{ q: string; a: string }> = [
  {
    q: 'Why a 14-day Team trial instead of 30?',
    a: 'B2B SaaS conversion data shows the 14→30 day lift is only 2–3 percentage points; most conversion happens in week 1–2 anyway. Our day-10 CSM nudge fires for tenants with ≥3 activated users + no upgrade click yet — that touchpoint is the best conversion signal we get. Need longer? Reach out, we comp 30 days on a call.',
  },
  {
    q: 'What does Free actually include?',
    a: 'The whole browser extension: PII detection, role library, cross-LLM portability via Markdown files, the in-page sidebar. Free is permanent and ungated — it covers ~70% of Pro\'s value. You upgrade to Pro when you get a second device and want roles synced across them.',
  },
  {
    q: 'Will my prompt content leave my browser?',
    a: 'No prompt body ever leaves your browser. The extension sends SHA-256 hashes of prompts, detection categories, action taken (redacted / flagged / logged), severity, and timestamps — never the prompt itself. Atlas dashboard tracks the metadata; the raw text stays on your machine.',
  },
  {
    q: 'How do Microsoft integrations work?',
    a: 'One Azure AD multi-tenant app, four scope sets. Connect Entra ID (the recommended first one) for SSO + user/group sync; add Intune for policy push, Purview for DLP event ingestion, and Defender for Cloud Apps for shadow-AI detection. All four share the same callback and refresh-token rotation.',
  },
  {
    q: 'Is the Slack bot intrusive?',
    a: 'The bot DMs a 5-question registration survey, opt-out with STOP at any time, GDPR right-to-erasure via chat.delete. We default to one survey per user per 14 days; admin can override. Bot responses are reviewed by the IT lead before becoming registered use cases.',
  },
  {
    q: 'What about AWS and GCP?',
    a: 'Coming in v0.2. The Integrations grid already shows AWS Bedrock, IAM Identity Center, Vertex AI, and Workspace as Coming Soon cards — click "Notify me" and we\'ll email when each ships.',
  },
];

function TierCard({ tier }: { tier: Tier }) {
  return (
    <div
      className={
        tier.emphasis
          ? 'flex flex-col rounded-2xl border-2 border-primary-500 bg-white p-6 shadow-md ring-1 ring-primary-200'
          : 'flex flex-col rounded-2xl border border-gray-200 bg-white p-6 shadow-sm'
      }
    >
      {tier.emphasis && (
        <span className="self-start rounded-full bg-primary-600 px-2.5 py-0.5 text-[11px] font-semibold text-white">
          Most popular
        </span>
      )}
      <h3 className="mt-2 text-lg font-bold text-gray-900">{tier.name}</h3>
      <p className="text-xs text-gray-500">{tier.audience}</p>

      <div className="mt-5">
        <span className="text-3xl font-bold text-gray-900">{tier.monthly}</span>
        <span className="ml-1 text-xs text-gray-500">{tier.monthlyDetail}</span>
      </div>
      <p className="mt-1 text-[11px] font-medium text-emerald-700">
        {tier.trial}
      </p>

      <Link
        href={tier.cta.href}
        className={
          tier.emphasis
            ? 'mt-5 rounded-md bg-primary-600 px-3 py-2 text-center text-sm font-semibold text-white hover:bg-primary-700'
            : 'mt-5 rounded-md border border-gray-300 bg-white px-3 py-2 text-center text-sm font-semibold text-gray-700 hover:bg-gray-50'
        }
      >
        {tier.cta.label} →
      </Link>

      <ul className="mt-6 space-y-2">
        {tier.features.map((f) => (
          <li key={f} className="flex items-start gap-2 text-sm text-gray-700">
            <span className="mt-0.5 text-emerald-500" aria-hidden="true">
              ✓
            </span>
            <span>{f}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function PricingPage() {
  return (
    <div className="min-h-screen bg-white text-gray-900">
      {/* Top nav */}
      <header className="border-b border-gray-100">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
          <Link href="/" className="text-xl font-bold text-primary-700">
            atlas
          </Link>
          <nav className="flex items-center gap-5 text-sm">
            <Link href="/pricing" className="font-semibold text-gray-900">
              Pricing
            </Link>
            <Link href="/login" className="rounded-md bg-primary-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-primary-700">
              Sign in
            </Link>
          </nav>
        </div>
      </header>

      {/* Header */}
      <section className="mx-auto max-w-6xl px-6 py-16 text-center">
        <h1 className="text-3xl font-bold text-gray-900 sm:text-4xl">
          Simple, transparent pricing.
        </h1>
        <p className="mx-auto mt-3 max-w-xl text-base text-gray-600">
          Free forever for individuals. Team plan unlocks the full
          atlas dashboard for orgs. Enterprise adds SSO / SCIM /
          on-prem. No per-seat-tier gotchas.
        </p>
      </section>

      {/* Tier grid */}
      <section className="mx-auto max-w-6xl px-6 pb-16">
        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4">
          {TIERS.map((t) => (
            <TierCard key={t.name} tier={t} />
          ))}
        </div>
      </section>

      {/* Annual band */}
      <section className="border-y border-gray-100 bg-gray-50">
        <div className="mx-auto max-w-6xl px-6 py-10 text-center">
          <p className="text-sm text-gray-700">
            <strong>Annual billing</strong> saves 20% on Pro and 24% on
            Team. Toggle in checkout. Enterprise is annual-only.
          </p>
        </div>
      </section>

      {/* FAQ */}
      <section className="mx-auto max-w-3xl px-6 py-16">
        <h2 className="text-center text-2xl font-bold text-gray-900">
          Frequently asked
        </h2>
        <dl className="mt-8 space-y-6">
          {FAQ.map((f) => (
            <div
              key={f.q}
              className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200"
            >
              <dt className="text-base font-semibold text-gray-900">{f.q}</dt>
              <dd className="mt-2 text-sm text-gray-700">{f.a}</dd>
            </div>
          ))}
        </dl>
      </section>

      {/* CTA strip */}
      <section className="border-t border-gray-100 bg-primary-50">
        <div className="mx-auto max-w-6xl px-6 py-12 text-center">
          <h2 className="text-2xl font-bold text-gray-900">
            Ready in 60 seconds.
          </h2>
          <p className="mt-2 text-sm text-gray-700">
            Sign in, connect Microsoft Entra ID, dispatch your first
            survey before lunch.
          </p>
          <Link
            href="/login"
            className="mt-5 inline-block rounded-md bg-primary-600 px-6 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-primary-700"
          >
            Sign in →
          </Link>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-gray-100">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-3 px-6 py-8 text-xs text-gray-500 sm:flex-row">
          <p>© {new Date().getFullYear()} atlas.</p>
          <div className="flex gap-4">
            <Link href="/" className="hover:text-gray-700">
              Home
            </Link>
            <a
              href="https://github.com/jun-bit-pulse-ai/atlas.ai"
              className="hover:text-gray-700"
              target="_blank"
              rel="noopener noreferrer"
            >
              GitHub
            </a>
            <Link href="/login" className="hover:text-gray-700">
              Sign in
            </Link>
          </div>
        </div>
      </footer>
    </div>
  );
}
