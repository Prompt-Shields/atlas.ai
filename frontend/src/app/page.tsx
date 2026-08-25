'use client';

// ─────────────────────────────────────────────────────────────────────
// Public marketing homepage — atlas.ai
//
// Replaces the prior bare redirect to /login. Unauthenticated visitors
// land here; the page surfaces the product proposition, three feature
// pillars, an integrations strip, and CTAs to /login + /pricing. If
// the visitor is already authenticated, the top nav swaps "Sign in"
// for a "Go to dashboard" CTA.
//
// No external CMS — content lives inline. When marketing copy needs
// to change, edit this file. Brand colour comes from the existing
// Tailwind primary-* scale.
// ─────────────────────────────────────────────────────────────────────

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';

const FEATURE_PILLARS = [
  {
    title: 'Detect shadow AI before it leaks data',
    body:
      'The Promptly browser extension catches PII the moment it hits an LLM input — at the keystroke, before submission. 11 categories of detector, Luhn-validated card numbers, common-bigram filtered names, all client-side.',
    bullet: '<50ms p95 detection',
  },
  {
    title: 'Coach in-line, never block the work',
    body:
      'A Grammarly-style sidebar with redact / coach / block actions per issue, not a modal that interrupts. Per-LLM role files preserve context across ChatGPT, Claude, Gemini, Copilot. Same role, every tool.',
    bullet: '6 default seed roles · import/export .md',
  },
  {
    title: 'Govern the program, not the people',
    body:
      'Atlas gives IT leads one screen for who uses what, where sensitive data lands, which policies are in force. Cross-LLM visibility, Microsoft Entra ID sync, Slack survey bot for use-case registration, audit PDFs for the board.',
    bullet: '13 dashboard pages · 8 integrations · OAuth + SSO',
  },
];

const INTEGRATIONS = [
  'Microsoft Entra ID',
  'Microsoft Intune',
  'Microsoft Purview',
  'Defender for Cloud Apps',
  'Slack',
  'AWS Bedrock (soon)',
  'Google Workspace (soon)',
];

export default function HomePage() {
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    setAuthed(api.isAuthenticated);
  }, []);

  return (
    <div className="min-h-screen bg-white text-gray-900">
      {/* Top nav */}
      <header className="border-b border-gray-100">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
          <Link href="/" className="text-xl font-bold text-primary-700">
            atlas
          </Link>
          <nav className="flex items-center gap-5 text-sm">
            <Link
              href="/pricing"
              className="text-gray-600 hover:text-gray-900"
            >
              Pricing
            </Link>
            <a
              href="https://github.com/jun-bit-pulse-ai/atlas.ai"
              className="text-gray-600 hover:text-gray-900"
              target="_blank"
              rel="noopener noreferrer"
            >
              GitHub
            </a>
            {authed ? (
              <Link
                href="/dashboard"
                className="rounded-md bg-primary-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-primary-700"
              >
                Go to dashboard →
              </Link>
            ) : (
              <Link
                href="/login"
                className="rounded-md bg-primary-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-primary-700"
              >
                Sign in
              </Link>
            )}
          </nav>
        </div>
      </header>

      {/* Hero */}
      <section className="mx-auto max-w-6xl px-6 py-20 text-center">
        <span className="inline-block rounded-full bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-700 ring-1 ring-emerald-200">
          AI Governance · Microsoft 365 first
        </span>
        <h1 className="mt-5 text-4xl font-bold tracking-tight text-gray-900 sm:text-5xl">
          See — and shape — how your team
          <br />
          uses every AI tool.
        </h1>
        <p className="mx-auto mt-5 max-w-2xl text-base text-gray-600 sm:text-lg">
          Atlas is the AI governance layer for Microsoft 365 orgs. One
          dashboard for shadow-AI discovery, in-line PII coaching,
          per-LLM role files, Slack-bot use-case registration, and
          board-ready audit PDFs.
        </p>
        <div className="mt-8 flex items-center justify-center gap-3">
          <Link
            href="/login"
            className="rounded-md bg-primary-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-primary-700"
          >
            Start free
          </Link>
          <Link
            href="/pricing"
            className="rounded-md border border-gray-300 bg-white px-5 py-2.5 text-sm font-semibold text-gray-700 hover:bg-gray-50"
          >
            See pricing
          </Link>
        </div>
        <p className="mt-3 text-xs text-gray-500">
          No credit card · Free tier forever · 14-day Team trial
        </p>
      </section>

      {/* Three pillars */}
      <section className="border-t border-gray-100 bg-gray-50">
        <div className="mx-auto max-w-6xl px-6 py-16">
          <h2 className="text-center text-xs font-semibold uppercase tracking-wide text-primary-700">
            How it works
          </h2>
          <p className="mx-auto mt-2 max-w-2xl text-center text-2xl font-bold text-gray-900">
            Three layers, one product
          </p>
          <div className="mt-10 grid grid-cols-1 gap-6 md:grid-cols-3">
            {FEATURE_PILLARS.map((p) => (
              <div
                key={p.title}
                className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200"
              >
                <h3 className="text-base font-semibold text-gray-900">
                  {p.title}
                </h3>
                <p className="mt-2 text-sm text-gray-600">{p.body}</p>
                <p className="mt-4 inline-block rounded-full bg-primary-50 px-2.5 py-1 text-[11px] font-medium text-primary-700">
                  {p.bullet}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Integrations strip */}
      <section className="border-t border-gray-100">
        <div className="mx-auto max-w-6xl px-6 py-16 text-center">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-primary-700">
            Integrations
          </h2>
          <p className="mt-2 text-2xl font-bold text-gray-900">
            Plugs into the stack you already run
          </p>
          <p className="mx-auto mt-3 max-w-xl text-sm text-gray-600">
            Microsoft Entra ID, Intune, Purview, and Defender for Cloud
            Apps share a single Azure AD app. Slack ships its own OAuth.
            AWS and GCP land in v0.2.
          </p>
          <ul className="mt-8 flex flex-wrap justify-center gap-2">
            {INTEGRATIONS.map((i) => (
              <li
                key={i}
                className="rounded-full bg-gray-100 px-4 py-1.5 text-sm font-medium text-gray-700"
              >
                {i}
              </li>
            ))}
          </ul>
        </div>
      </section>

      {/* Big CTA */}
      <section className="border-t border-gray-100 bg-primary-50">
        <div className="mx-auto flex max-w-6xl flex-col items-center gap-3 px-6 py-14 text-center sm:flex-row sm:justify-between sm:text-left">
          <div>
            <h2 className="text-2xl font-bold text-gray-900">
              Ready to see what your team is doing with AI?
            </h2>
            <p className="mt-1 text-sm text-gray-700">
              60-second onboarding. Microsoft Entra ID is the
              recommended first connection.
            </p>
          </div>
          <Link
            href="/login"
            className="rounded-md bg-primary-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-primary-700"
          >
            Sign in to start →
          </Link>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-gray-100">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-3 px-6 py-8 text-xs text-gray-500 sm:flex-row">
          <p>
            © {new Date().getFullYear()} atlas. AI governance for
            Microsoft 365 orgs.
          </p>
          <div className="flex gap-4">
            <Link href="/pricing" className="hover:text-gray-700">
              Pricing
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
