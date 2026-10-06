'use client';

import { useEffect, useState } from 'react';
import {
  guideApi,
  type GuideFigure,
  type GuidePilotMonth,
  type GuideRiskRow,
  type PilotReportResult,
} from '@/lib/guide';

// The 30-day pilot report (promptly-guide #39): which AI tools are used, where
// people get stuck, and what risky behaviour appeared — from aggregate data only.
// Everything shown arrives already gated by the backend; this page adds nothing finer.

const ENDINGS: Record<string, string> = {
  stopped: 'Stopped by the person',
  timedOut: 'Left unanswered',
  cannotContinue: 'Guide could not see a way on',
  tooManySteps: 'Ran out of steps',
  stuck: 'A step did not work',
  planFailed: 'Guide could not plan the next step',
};

const ACTIONS: Record<string, string> = {
  allowed: 'Allowed',
  logged: 'Logged',
  redacted: 'Redacted',
  flagged: 'Flagged',
  blocked: 'Blocked',
};

function FigureTable({
  title,
  figures,
  label,
  empty,
}: {
  title: string;
  figures: GuideFigure[];
  label: (f: GuideFigure) => string;
  empty: string;
}) {
  return (
    <div className="mb-6">
      <h4 className="text-sm font-semibold text-gray-800 mb-2">{title}</h4>
      {figures.length === 0 ? (
        <p className="text-sm text-gray-500">{empty}</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-gray-500 border-b border-gray-200">
              <th className="py-1 pr-4 font-medium">Team</th>
              <th className="py-1 pr-4 font-medium"></th>
              <th className="py-1 font-medium">Share of the team</th>
            </tr>
          </thead>
          <tbody>
            {figures.map((f) => (
              <tr
                key={`${f.team}/${f.category_kind}/${f.category_id}`}
                className="border-b border-gray-100"
              >
                <td className="py-1 pr-4">{f.team}</td>
                <td className="py-1 pr-4">{label(f)}</td>
                <td className="py-1 tabular-nums">{f.band}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function RiskTable({ title, rows, label }: { title: string; rows: GuideRiskRow[]; label?: (k: string) => string }) {
  return (
    <div>
      <h4 className="text-sm font-semibold text-gray-800 mb-2">{title}</h4>
      {rows.length === 0 ? (
        <p className="text-sm text-gray-500">Nothing to show.</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-gray-500 border-b border-gray-200">
              <th className="py-1 pr-4 font-medium"></th>
              <th className="py-1 pr-4 font-medium text-right">Events</th>
              <th className="py-1 font-medium text-right">Devices</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} className="border-b border-gray-100">
                <td className="py-1 pr-4">{label ? label(r.key) : r.key}</td>
                <td className="py-1 pr-4 text-right tabular-nums">{r.events}</td>
                <td className="py-1 text-right tabular-nums">{r.devices}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function MonthCard({ month, minimum }: { month: GuidePilotMonth; minimum: number }) {
  const left = Object.values(month.suppressed_categories).reduce((a, b) => a + b, 0);
  return (
    <section className="bg-white border border-gray-200 rounded-lg p-6 mb-6">
      <h3 className="text-lg font-semibold mb-4">{month.period}</h3>
      <FigureTable
        title="AI tools in use"
        figures={month.tools}
        label={(f) => f.category_id}
        empty="No figure this month."
      />
      <FigureTable
        title="Walkthroughs finished"
        figures={month.finished}
        label={() => 'Finished'}
        empty="No figure this month."
      />
      <FigureTable
        title="Where walkthroughs stopped"
        figures={month.not_finished}
        label={(f) => ENDINGS[f.category_id] ?? f.category_id}
        empty="No ending reached enough people to show."
      />
      <FigureTable
        title="What people asked Guide about"
        figures={month.topics}
        label={(f) => f.category_id}
        empty="No topic reached enough people to show."
      />
      {(month.teams_too_small.length > 0 || left > 0) && (
        <p className="text-xs text-gray-500">
          {month.teams_too_small.length > 0 &&
            `${month.teams_too_small.length} team(s) had fewer than ${minimum} people counted and are not shown. `}
          {left > 0 && `${left} figure(s) were left out for being about too few people.`}
        </p>
      )}
    </section>
  );
}

export default function PilotReportPage() {
  const [result, setResult] = useState<PilotReportResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    guideApi
      .pilotReport()
      .then((r) => {
        if (!cancelled) setResult(r);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function download() {
    try {
      const text = await guideApi.pilotReportMarkdown();
      const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = 'promptly-guide-pilot-report.md';
      link.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="p-8 max-w-5xl">
      <div className="flex items-start justify-between gap-4 mb-2">
        <h1 className="text-3xl font-semibold">Pilot Report</h1>
        {result?.kind === 'ready' && (
          <button
            onClick={download}
            className="px-3 py-1.5 text-sm border border-gray-300 rounded-md hover:bg-gray-50"
          >
            Download as Markdown
          </button>
        )}
      </div>
      <p className="text-gray-600 mb-6">
        Which AI tools people use, where they get stuck with Promptly Guide, and what risky
        behaviour appeared during the pilot. Aggregate data only: no figure is about one person.
      </p>

      {error && <p className="text-red-600 mb-6">Could not load the report: {error}</p>}
      {!result && !error && <p className="text-gray-500">Loading…</p>}

      {result?.kind === 'notConnected' && (
        <p className="text-gray-700">
          Promptly Guide is not connected to this organisation yet. A tenant admin connects it
          with the organisation&apos;s Firebase project; the report is ready 30 days later.
        </p>
      )}

      {result?.kind === 'notReady' && (
        <p className="text-gray-700">
          The pilot report is ready 30 days after Promptly Guide was connected
          {result.readyOn ? `, on ${result.readyOn}` : ''}.
        </p>
      )}

      {result?.kind === 'ready' && (
        <>
          <p className="text-sm text-gray-500 mb-4">
            Guide connected {result.report.connected_at.slice(0, 10)} · report made{' '}
            {result.report.generated_at.slice(0, 10)}
          </p>
          <div className="bg-gray-50 border border-gray-200 rounded-lg p-4 mb-8 text-sm text-gray-700 space-y-2">
            {result.report.notes.map((note) => (
              <p key={note}>{note}</p>
            ))}
          </div>

          <h2 className="text-xl font-semibold mb-4">AI tools in use, and where people get stuck</h2>
          {result.report.months.length === 0 ? (
            <p className="text-gray-500 mb-8">No finished month yet.</p>
          ) : (
            result.report.months.map((m) => (
              <MonthCard key={m.period} month={m} minimum={result.report.minimum_group_size} />
            ))
          )}

          <h2 className="text-xl font-semibold mb-2">Risky behaviour</h2>
          <p className="text-sm text-gray-500 mb-4">
            {result.report.risk.since.slice(0, 10)} to {result.report.risk.until.slice(0, 10)}, the
            whole organisation. Rows from fewer than {result.report.risk.minimum_devices} devices
            are left out.
          </p>
          <section className="bg-white border border-gray-200 rounded-lg p-6 grid gap-8 md:grid-cols-3">
            <RiskTable title="Personal data in prompts, by kind" rows={result.report.risk.by_category} />
            <RiskTable title="By AI tool" rows={result.report.risk.by_app} />
            <RiskTable
              title="What was done about it"
              rows={result.report.risk.by_action}
              label={(k) => ACTIONS[k] ?? k}
            />
          </section>
          {Object.values(result.report.risk.suppressed).some((n) => n > 0) && (
            <p className="text-xs text-gray-500 mt-2">
              {Object.values(result.report.risk.suppressed).reduce((a, b) => a + b, 0)} row(s) were
              left out for spanning too few devices.
            </p>
          )}
        </>
      )}
    </div>
  );
}
