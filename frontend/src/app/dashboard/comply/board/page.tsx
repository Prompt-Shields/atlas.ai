'use client'

// /dashboard/comply/board — board-ready AI risk report (PRO-60).
//
// The single screen a Head of AI Risk can take to the board: an overall
// posture headline, the ranked top risks (with owners, frameworks, deadlines
// and remediation), framework coverage, and ISO 42001 certification
// readiness. Every figure is derived from the same single source of truth as
// the operational views (compliance-metrics + board-risks), so the board pack
// never contradicts the dashboard. "Export board pack" prints to PDF.

import { useEffect, useState } from 'react'
import Link from 'next/link'
import {
  ArrowLeft,
  Printer,
  TrendingDown,
  TrendingUp,
  Minus,
  AlertTriangle,
  ShieldCheck,
  Radar,
  UserX,
  ShieldAlert,
  Lock,
} from 'lucide-react'
import { FRAMEWORK_METRICS, AVERAGE_COVERAGE } from '@/lib/aispm/compliance-metrics'
import {
  TOP_RISKS,
  BOARD_COUNTS,
  overallPosture,
  RISK_APPETITE_PROFILE,
  type Severity,
} from '@/lib/aispm/board-risks'
import {
  ISO42001_STEP_COUNT,
  computeCoverage,
  isCertificationReady,
  readCompletedSteps,
} from '@/lib/aispm/iso42001-journey'

const SEVERITY_STYLE: Record<Severity, { chip: string; bar: string; label: string }> = {
  critical: { chip: 'bg-red-100 text-red-700', bar: 'bg-red-500', label: 'Critical' },
  high: { chip: 'bg-orange-100 text-orange-700', bar: 'bg-orange-500', label: 'High' },
  medium: { chip: 'bg-yellow-100 text-yellow-700', bar: 'bg-yellow-500', label: 'Medium' },
  low: { chip: 'bg-slate-100 text-slate-600', bar: 'bg-slate-400', label: 'Low' },
}

const POSTURE_STYLE: Record<string, string> = {
  Low: 'text-green-600',
  Moderate: 'text-amber-600',
  Elevated: 'text-orange-600',
  High: 'text-red-600',
}

function TrendIcon({ trend }: { trend: 'up' | 'down' | 'flat' }) {
  // For risk metrics, down = improving (good).
  if (trend === 'down') return <TrendingDown size={14} className="text-green-600" />
  if (trend === 'up') return <TrendingUp size={14} className="text-red-600" />
  return <Minus size={14} className="text-slate-400" />
}

export default function BoardReportPage() {
  const [isoCompleted, setIsoCompleted] = useState(0)
  const [asOf, setAsOf] = useState('')

  useEffect(() => {
    setIsoCompleted(readCompletedSteps().length)
    setAsOf(
      new Date().toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' }),
    )
  }, [])

  const posture = overallPosture()
  const isoCoverage = computeCoverage(isoCompleted)
  const isoReady = isCertificationReady(isoCompleted)

  const kpis = [
    { label: 'AI use cases', value: BOARD_COUNTS.totalUseCases, Icon: Radar },
    { label: 'GDPR gaps', value: BOARD_COUNTS.gdprGaps, Icon: Lock },
    { label: 'High-risk', value: BOARD_COUNTS.highRisk, Icon: ShieldAlert },
    { label: 'Unowned high-risk', value: BOARD_COUNTS.unownedHighRisk, Icon: UserX },
    { label: 'Shadow AI', value: BOARD_COUNTS.shadowAi, Icon: AlertTriangle },
    { label: 'Avg. coverage', value: `${AVERAGE_COVERAGE}%`, Icon: ShieldCheck },
  ]

  return (
    <div className="max-w-5xl">
      {/* Breadcrumb + actions (hidden in print) */}
      <div className="flex items-center justify-between mb-4 print:hidden">
        <Link
          href="/dashboard/comply"
          className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-500 hover:text-slate-700"
        >
          <ArrowLeft size={14} />
          Back to Compliance
        </Link>
        <button
          onClick={() => window.print()}
          className="inline-flex items-center gap-1.5 text-xs font-semibold bg-slate-900 hover:bg-slate-800 text-white px-3 py-1.5 rounded-lg"
        >
          <Printer size={13} />
          Export board pack
        </button>
      </div>

      {/* Title */}
      <div className="mb-6">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
          Prepared for the Board · AI risk &amp; compliance
        </p>
        <h1 className="text-2xl font-bold text-slate-900 mt-1">AI risk board report</h1>
        <p className="text-sm text-slate-500 mt-1">
          Top AI risks and compliance posture across the organisation{asOf ? ` · as of ${asOf}` : ''}.
          Derived live from the AI register — illustrative demo data.
        </p>
      </div>

      {/* Posture banner */}
      <div className="rounded-2xl border border-slate-200 bg-white shadow-sm p-6 mb-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
              Overall AI risk posture
            </p>
            <div className="flex items-baseline gap-3 mt-1">
              <span className={`text-3xl font-bold ${POSTURE_STYLE[posture.label]}`}>
                {posture.label}
              </span>
              <span className="inline-flex items-center gap-1 text-sm font-medium text-green-600">
                <TrendingUp size={14} />
                {posture.trend} vs last quarter
              </span>
            </div>
          </div>
          <div className="max-w-md">
            <span className="inline-flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-indigo-700 bg-indigo-100 px-2 py-0.5 rounded-full mb-2">
              <Lock size={11} />
              {RISK_APPETITE_PROFILE} risk appetite
            </span>
            <p className="text-sm text-slate-500 leading-relaxed">
              Top risks are ranked by fine &amp; privacy exposure — GDPR and financial-crime weighted
              highest, operational/availability and ESG weighted down to match the organisation&apos;s
              risk appetite.
            </p>
          </div>
        </div>

        {/* KPI strip */}
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mt-6">
          {kpis.map(({ label, value, Icon }) => (
            <div key={label} className="rounded-xl border border-slate-100 bg-slate-50/60 p-4">
              <div className="flex items-center gap-1.5 text-slate-400">
                <Icon size={13} />
                <span className="text-[10px] font-semibold uppercase tracking-wide">{label}</span>
              </div>
              <p className="mt-1 text-2xl font-bold text-slate-900 tabular-nums">{value}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Top risks */}
      <h2 className="text-sm font-semibold text-slate-700 mb-3">Top risks to report</h2>
      <ol className="space-y-3 mb-8">
        {TOP_RISKS.slice(0, 5).map((risk) => {
          const sev = SEVERITY_STYLE[risk.severity]
          return (
            <li
              key={risk.id}
              className="rounded-xl border border-slate-200 bg-white shadow-sm p-5 break-inside-avoid"
            >
              <div className="flex items-start gap-4">
                <div className="shrink-0 flex items-center justify-center w-8 h-8 rounded-full bg-slate-900 text-white text-sm font-bold">
                  {risk.rank}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-start justify-between gap-3 flex-wrap">
                    <div className="flex items-center gap-2 flex-wrap">
                      <h3 className="text-sm font-bold text-slate-900">{risk.title}</h3>
                      <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full ${sev.chip}`}>
                        {sev.label}
                      </span>
                    </div>
                    <div className="flex items-baseline gap-1.5">
                      <span className="text-2xl font-bold text-slate-900 tabular-nums">{risk.metric}</span>
                      <span className="text-xs text-slate-400">{risk.unit}</span>
                      <TrendIcon trend={risk.trend} />
                    </div>
                  </div>

                  <p className="text-sm text-slate-600 leading-relaxed mt-1.5">{risk.detail}</p>

                  <div className="flex items-center gap-1.5 flex-wrap mt-3">
                    {risk.frameworks.map((fw) => (
                      <span
                        key={fw}
                        className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-slate-100 text-slate-600"
                      >
                        {fw}
                      </span>
                    ))}
                  </div>

                  <div className="flex items-start justify-between gap-3 mt-3 flex-wrap">
                    <p className="text-xs text-slate-500">
                      <span className="font-semibold text-slate-600">Action:</span> {risk.remediation}
                    </p>
                    {risk.deadline && (
                      <span className="text-[10px] font-semibold text-red-600 bg-red-50 px-2 py-0.5 rounded-full">
                        Due {risk.deadline}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            </li>
          )
        })}
      </ol>

      {/* Framework coverage + ISO readiness */}
      <h2 className="text-sm font-semibold text-slate-700 mb-3">Compliance coverage</h2>
      <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-5 break-inside-avoid">
        <div className="space-y-4">
          {FRAMEWORK_METRICS.map((fw) => {
            const pct = fw.key === 'iso42001' ? isoCoverage : fw.percentage
            return (
              <div key={fw.key}>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs font-medium text-slate-700">
                    {fw.name}
                    {fw.key === 'iso42001' && isoReady && (
                      <span className="ml-2 text-[10px] font-semibold text-violet-700 bg-violet-100 px-2 py-0.5 rounded-full">
                        Certification-ready
                      </span>
                    )}
                  </span>
                  <span className="text-xs font-semibold text-slate-900 tabular-nums">{pct}%</span>
                </div>
                <div className="h-2 rounded-full bg-slate-100 overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-500"
                    style={{ width: `${pct}%`, backgroundColor: fw.color }}
                  />
                </div>
              </div>
            )
          })}
        </div>
        <p className="text-xs text-slate-500 mt-4 print:hidden">
          ISO/IEC 42001 readiness reflects the guided journey
          {isoReady
            ? ' — all clauses complete.'
            : ` (${isoCompleted}/${ISO42001_STEP_COUNT} clauses complete).`}{' '}
          <Link href="/dashboard/comply/iso-42001" className="font-medium text-violet-600 hover:underline">
            Open the ISO 42001 journey →
          </Link>
        </p>
      </div>

      <p className="text-[11px] text-slate-400 mt-6">
        Generated from the live AI register. Figures are illustrative demo data and consistent across
        the Compliance overview, the ISO 42001 journey, and this report.
      </p>
    </div>
  )
}
