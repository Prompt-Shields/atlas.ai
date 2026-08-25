'use client';

// ─────────────────────────────────────────────────────────────────────
// AI Governance Handbook — /dashboard/handbook
//
// Customer-driven: Øystein Endal (AI Risk Officer) flagged training
// + awareness as the #1 priority — "burde bruke mer tid på det"
// (should spend more time on it). This is atlas's answer.
//
// The page is intentionally long-form. It answers the question every
// employee silently asks when an IT lead rolls out atlas:
//   • Why is my org doing this?
//   • What does atlas actually see?
//   • Is my data private?
//   • What's expected of me?
//
// Each section is short and concrete. At the bottom: an Acknowledge
// button that POSTs /api/v1/handbook/acknowledge and records the
// per-user acceptance. The button changes to a green "Acknowledged on
// <date>" state once recorded.
//
// Tenant admins also see a small coverage widget at the top showing
// how many staff have acknowledged the current version.
// ─────────────────────────────────────────────────────────────────────

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import { ORG } from '@/lib/curated-demo-data';
import type {
  HandbookStatusResponse,
  HandbookTenantStats,
} from '@/lib/types';

// ─── Sub-components ───────────────────────────────────────────────────


function CoverageWidget({
  stats,
}: {
  stats: HandbookTenantStats;
}) {
  const pct =
    stats.total_users > 0
      ? Math.round(
          (stats.acknowledged_current_count / stats.total_users) * 100,
        )
      : 0;
  return (
    <div className="mt-4 rounded-lg bg-blue-50 px-4 py-3 ring-1 ring-blue-100">
      <p className="text-sm text-blue-900">
        <strong>
          {stats.acknowledged_current_count} of {stats.total_users}
        </strong>{' '}
        staff ({pct}%) have acknowledged the handbook (v{stats.current_version}).
        {stats.acknowledged_any_count >
          stats.acknowledged_current_count && (
          <span className="ml-1 text-blue-700">
            +
            {stats.acknowledged_any_count -
              stats.acknowledged_current_count}{' '}
            on an older version.
          </span>
        )}
      </p>
    </div>
  );
}


function Section({
  number,
  title,
  children,
}: {
  number: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mt-8 first:mt-0">
      <h2 className="text-base font-semibold text-gray-900">
        <span className="mr-2 text-primary-600">{number}</span>
        {title}
      </h2>
      <div className="mt-2 space-y-3 text-sm leading-relaxed text-gray-700">
        {children}
      </div>
    </section>
  );
}


function AckPanel({
  status,
  onAcknowledge,
  saving,
}: {
  status: HandbookStatusResponse | null;
  onAcknowledge: (notes: string | undefined) => void;
  saving: boolean;
}) {
  const [notes, setNotes] = useState('');

  if (status?.acknowledged && status.acknowledged_at) {
    return (
      <div className="rounded-xl bg-emerald-50 px-5 py-4 ring-1 ring-emerald-100">
        <p className="text-sm font-semibold text-emerald-900">
          ✓ You acknowledged this handbook on{' '}
          {new Date(status.acknowledged_at).toLocaleDateString(undefined, {
            year: 'numeric',
            month: 'long',
            day: 'numeric',
          })}
        </p>
        <p className="mt-1 text-xs text-emerald-800">
          Thanks. You&apos;re ready to continue using AI tools with atlas&apos;s
          in-line coaching. If the org publishes a new version of this
          handbook, you&apos;ll see an updated prompt here.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
      <h3 className="text-sm font-semibold text-gray-900">
        Confirm you&apos;ve read this
      </h3>
      <p className="mt-1 text-xs text-gray-600">
        Acknowledging tells your IT lead you understand how atlas works
        and why it&apos;s running. The button records your name + the
        current date.
      </p>
      <label className="mt-3 block">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">
          Optional notes
        </span>
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={2}
          placeholder="Any questions or feedback for your IT lead?"
          className="mt-1 w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-900 placeholder:text-gray-400 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
        />
      </label>
      <div className="mt-4 flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={() => onAcknowledge(notes.trim() || undefined)}
          disabled={saving}
          className="rounded-md bg-primary-600 px-4 py-2 text-sm font-semibold text-white hover:bg-primary-700 disabled:cursor-not-allowed disabled:bg-gray-300"
        >
          {saving ? 'Saving…' : 'I&apos;ve read this — acknowledge'}
        </button>
      </div>
    </div>
  );
}


// ─── Page ─────────────────────────────────────────────────────────────


export default function HandbookPage() {
  const [status, setStatus] = useState<HandbookStatusResponse | null>(null);
  const [stats, setStats] = useState<HandbookTenantStats | null>(null);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  // Refresh handbook status + tenant coverage (soft-fail).
  const refresh = async () => {
    try {
      const s = await api.getHandbookStatus();
      setStatus(s);
    } catch {
      /* keep null — non-admins see the read-only handbook */
    }
    try {
      const t = await api.getHandbookTenantStats();
      setStats(t);
    } catch {
      /* user is not an admin or backend offline — hide coverage */
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const onAcknowledge = async (notes: string | undefined) => {
    setSaving(true);
    try {
      await api.acknowledgeHandbook(notes);
      setToast('✓ Thanks — acknowledgement recorded.');
      await refresh();
    } catch {
      setToast('Acknowledgement failed — try again in a moment.');
    } finally {
      setSaving(false);
    }
  };

  const tocItems = useMemo(
    () => [
      { id: 'why', label: 'Why we have AI governance' },
      { id: 'what-atlas-does', label: 'What atlas actually does' },
      { id: 'your-data', label: 'What atlas sees about you' },
      { id: 'expected', label: "What's expected of you" },
      { id: 'tools', label: 'Approved AI tools' },
      { id: 'flag', label: 'How to flag concerns' },
      { id: 'rights', label: 'Your rights as a user' },
      { id: 'changes', label: 'How this handbook changes' },
    ],
    [],
  );

  return (
    <div className="mx-auto max-w-4xl">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">
          AI governance — the handbook
        </h1>
        <p className="mt-1 text-sm text-gray-600">
          {ORG.name} · v{status?.current_version ?? '1.0'} · 5-minute read
        </p>
        {stats && <CoverageWidget stats={stats} />}
      </div>

      {/* Table of contents */}
      <nav className="mt-6 rounded-lg bg-gray-50 p-4 ring-1 ring-gray-200">
        <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
          Contents
        </p>
        <ol className="mt-2 grid grid-cols-1 gap-x-4 gap-y-1 text-sm text-primary-700 sm:grid-cols-2">
          {tocItems.map((item, i) => (
            <li key={item.id}>
              <a href={`#${item.id}`} className="hover:underline">
                {String(i + 1).padStart(2, '0')}. {item.label}
              </a>
            </li>
          ))}
        </ol>
      </nav>

      <article className="mt-8 rounded-xl bg-white p-8 shadow-sm ring-1 ring-gray-200">
        <Section number="01" title="Why we have AI governance">
          <p>
            Your colleagues use AI tools every day — ChatGPT, Copilot,
            Claude, Gemini — to draft documents, summarise emails,
            review code, analyse spreadsheets. That&apos;s great. AI
            removes friction from work you used to dread.
          </p>
          <p>
            But the same tools that save time also accept{' '}
            <strong>anything you paste</strong>. When you paste a
            grantee&apos;s social security number into ChatGPT, that
            number leaves the building. It joins a model provider&apos;s
            training pipeline. It may show up in someone else&apos;s
            answer. It&apos;s out of your control, and you can&apos;t
            take it back.
          </p>
          <p>
            AI governance is how {ORG.name} avoids the second
            outcome without losing the first. We&apos;re not blocking
            you — we&apos;re coaching you, in the moment, at the
            keystroke, so the sensitive data stays where it belongs.
          </p>
        </Section>

        <Section number="02" title="What atlas actually does">
          <p>
            Atlas runs in two places:
          </p>
          <ul className="ml-5 list-disc space-y-1">
            <li>
              <strong>Your browser</strong>, as the Promptly extension.
              When you type into ChatGPT or any of the 30+ supported AI
              sites, the extension runs a local detector that looks for
              11 categories of sensitive data: emails, phone numbers,
              credit cards, SSNs, IPs, API keys, JWT tokens, IBAN
              numbers, Bitcoin addresses, currency amounts, and names.
            </li>
            <li>
              <strong>Your IT lead&apos;s dashboard</strong>, where atlas
              aggregates patterns: which tools the team uses, where
              redactions happen, which departments most often hit
              sensitive content.
            </li>
          </ul>
          <p>
            When a sensitive value shows up in your input, atlas does{' '}
            <strong>one of three things</strong>:
          </p>
          <ul className="ml-5 list-disc space-y-1">
            <li>
              <strong>Coach</strong> — show you a small banner
              suggesting a safer way to phrase the request
            </li>
            <li>
              <strong>Redact</strong> — replace the value with a
              placeholder (e.g. <code className="rounded bg-gray-100 px-1">[SSN]</code>) before send
            </li>
            <li>
              <strong>Block</strong> — only for compensation, banking,
              and other regulated data classes
            </li>
          </ul>
          <p>
            You can override any of these. The override is logged but
            never punitive — the point is to make you stop and think,
            not to stop you from working.
          </p>
        </Section>

        <Section number="03" title="What atlas sees about you">
          <p>
            Atlas <strong>never sees the body of your prompt</strong>.
            When the in-browser detector finds sensitive content, it
            emits a tiny event to {ORG.name}&apos;s atlas backend
            containing:
          </p>
          <ul className="ml-5 list-disc space-y-1">
            <li>
              A <strong>SHA-256 hash</strong> of the prompt — a one-way
              fingerprint, not reversible to the original text
            </li>
            <li>
              The detection categories that triggered (e.g.{' '}
              <code className="rounded bg-gray-100 px-1">email</code>,{' '}
              <code className="rounded bg-gray-100 px-1">creditCard</code>)
            </li>
            <li>
              The action you (or atlas) took: coached, redacted,
              blocked, logged
            </li>
            <li>
              The application: ChatGPT, Copilot, Claude, etc.
            </li>
            <li>A timestamp + your atlas user id</li>
          </ul>
          <p>
            <strong>Atlas never receives</strong>: the prompt body,
            redacted prompts, the model&apos;s response, your secret
            values, authentication tokens, or anything you typed that
            wasn&apos;t flagged.
          </p>
          <p>
            This is a deliberate design choice. Your IT lead asks atlas
            for patterns, not transcripts.
          </p>
        </Section>

        <Section number="04" title="What's expected of you">
          <p>The short version: pay attention to the coaching nudges.</p>
          <p>
            The slightly longer version:
          </p>
          <ul className="ml-5 list-disc space-y-1">
            <li>
              When atlas suggests a redaction, take it unless you have
              a clear reason not to.
            </li>
            <li>
              When you start a new AI workflow that handles sensitive
              data — grantee files, financials, donor records, HR
              materials — let your IT lead know. The Slack survey-bot
              makes this a 90-second task; just answer the DM.
            </li>
            <li>
              Don&apos;t paste compensation, bank details, or other
              regulated data into AI tools, even with atlas running.
              Use approved internal systems for those.
            </li>
            <li>
              If you&apos;re unsure whether something&apos;s safe to
              paste — ask. Section 06 below tells you who.
            </li>
          </ul>
        </Section>

        <Section number="05" title="Approved AI tools">
          <p>
            {ORG.name} has a tiered approach. The list lives at{' '}
            <Link
              href="/dashboard/registry"
              className="font-medium text-primary-600 hover:text-primary-700"
            >
              /dashboard/registry
            </Link>{' '}
            (IT-lead-managed) and is updated whenever the IT lead
            approves a new use case.
          </p>
          <p>
            <strong>Approved out of the box:</strong> Microsoft Copilot
            (when accessed via your work account), the AI features
            inside Microsoft 365 apps, and anything atlas tags as{' '}
            <em>sanctioned</em> in its discovery feed.
          </p>
          <p>
            <strong>Use with caution:</strong> Public ChatGPT, Claude,
            Gemini, Perplexity. These work fine for non-sensitive
            tasks. Atlas runs alongside and coaches you when you stray
            into regulated content.
          </p>
          <p>
            <strong>Not approved:</strong> Any tool that handles
            grantee, donor, or beneficiary data and isn&apos;t in the
            registry. If you&apos;re unsure, ask first.
          </p>
        </Section>

        <Section number="06" title="How to flag concerns">
          <p>
            You can raise an issue three ways:
          </p>
          <ul className="ml-5 list-disc space-y-1">
            <li>
              <strong>Slack</strong> — DM <code>@AtlasBot</code> with
              your concern. The bot opens an issue in the IT lead&apos;s
              queue.
            </li>
            <li>
              <strong>Email</strong> — your IT lead has set this up at
              the org level. Atlas doesn&apos;t hard-code an address —
              ask your IT lead.
            </li>
            <li>
              <strong>This page</strong> — leave a note in the
              acknowledgement field below. The IT lead reads every
              note.
            </li>
          </ul>
          <p>
            <strong>If you think atlas misbehaved</strong> — flagged
            something incorrectly, or didn&apos;t flag something it
            should have — please tell us. The detector improves only
            with real-world feedback.
          </p>
        </Section>

        <Section number="07" title="Your rights as a user">
          <p>
            You have the right to:
          </p>
          <ul className="ml-5 list-disc space-y-1">
            <li>
              <strong>Know what data atlas has on you</strong> — request
              a per-user export of your atlas activity, including every
              detection event tied to your account.
            </li>
            <li>
              <strong>Have it erased</strong> — under GDPR (and
              equivalent statutes), you can request deletion of your
              atlas activity records. Survey responses you submitted
              via the bot can be deleted; aggregate counts the IT lead
              already saw cannot be retroactively reversed.
            </li>
            <li>
              <strong>Opt out of optional features</strong> — the
              survey bot has a <code>STOP</code> command. The atlas
              extension itself, however, is part of your org&apos;s
              security posture and isn&apos;t individually opt-out.
            </li>
            <li>
              <strong>See this handbook</strong> — you&apos;re reading
              it. The version is recorded; if the org meaningfully
              updates it, you&apos;ll see a re-acknowledgement prompt
              here.
            </li>
          </ul>
        </Section>

        <Section number="08" title="How this handbook changes">
          <p>
            We track the handbook version (currently{' '}
            <strong>v{status?.current_version ?? '1.0'}</strong>) and
            record your acknowledgement against that version. If the
            org publishes new content that changes meaning — not just
            typo fixes — the version bumps and you&apos;ll see a fresh
            prompt the next time you open the dashboard.
          </p>
          <p>
            Minor copy edits don&apos;t bump the version. Substantive
            changes — new tool categories, new policy obligations,
            new rights you have — do.
          </p>
          <p className="rounded-md bg-amber-50 p-3 text-xs text-amber-900 ring-1 ring-amber-100">
            <strong>Questions about anything in this handbook?</strong>{' '}
            Drop them in the notes below before acknowledging. Your IT
            lead will see them.
          </p>
        </Section>
      </article>

      {/* Acknowledge panel */}
      <div className="mt-6">
        <AckPanel
          status={status}
          onAcknowledge={onAcknowledge}
          saving={saving}
        />
      </div>

      <p className="mt-4 text-xs text-gray-500">
        Linked from{' '}
        <Link
          href="/dashboard/coaching"
          className="font-medium text-primary-600 hover:text-primary-700"
        >
          /coaching
        </Link>{' '}
        and the onboarding wizard. Tenant admins see coverage stats at
        the top of this page.
      </p>

      {toast && (
        <div
          className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-lg bg-gray-900 px-4 py-2 text-sm text-white shadow-xl"
          role="status"
        >
          {toast}
        </div>
      )}
    </div>
  );
}
