'use client'

// /dashboard/comply — AI compliance hub (PRO-60).
//
// Ported from ai-spm-dashboard/app/comply/page.tsx. Framework coverage,
// the ISO 42001 guided-journey CTA, the coverage breakdown bar, priority
// remediations and the gap table. Every coverage figure derives from the
// single source of truth (compliance-metrics); ISO 42001 reflects the
// guided journey's localStorage progress.

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { ArrowRight } from 'lucide-react'
import { USE_CASES } from '@/lib/aispm/use-cases'
import { ComplianceCoverageCard } from '@/components/spm/compliance-coverage-card'
import { CoverageBar } from '@/components/spm/coverage-bar'
import { FRAMEWORK_METRICS, frameworkLevel } from '@/lib/aispm/compliance-metrics'
import {
  ISO42001_STEP_COUNT,
  computeCoverage,
  computeGapCount,
  isCertificationReady,
  isoCensus,
  readCompletedSteps,
} from '@/lib/aispm/iso42001-journey'

const LEVEL_BADGE: Record<string, string> = {
  covered: 'bg-emerald-100 text-emerald-700',
  partial: 'bg-amber-100 text-amber-700',
  gap: 'bg-red-100 text-red-700',
}

export default function ComplyPage() {
  // ISO 42001 coverage climbs as the guided journey is completed
  // (persisted to localStorage). Hydrated after mount to avoid SSR drift.
  const [isoCompleted, setIsoCompleted] = useState(0)
  useEffect(() => {
    setIsoCompleted(readCompletedSteps().length)
    const onFocus = () => setIsoCompleted(readCompletedSteps().length)
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [])
  const isoReady = isCertificationReady(isoCompleted)

  // Top framework cards — all derived from the single source of truth
  // (compliance-metrics), with ISO 42001 reflecting journey progress.
  const frameworkCards = FRAMEWORK_METRICS.map(fw =>
    fw.key === 'iso42001'
      ? {
          ...fw,
          ...isoCensus(isoCompleted),
          percentage: computeCoverage(isoCompleted),
          gapCount: computeGapCount(isoCompleted),
        }
      : fw,
  )

  const gapUseCases = USE_CASES.filter(uc =>
    frameworkLevel(uc, 'gdpr') === 'gap' ||
    uc.complianceStatus.euAiAct === 'gap' ||
    uc.complianceStatus.nistAiRmf === 'gap' ||
    uc.complianceStatus.owaspLlm === 'gap'
  ).slice(0, 12)

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold text-slate-900">AI Compliance</h1>
          <p className="text-sm text-slate-500 mt-0.5">Framework coverage across all AI use cases</p>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href="/dashboard/comply/frameworks"
            className="text-xs font-semibold bg-white border border-slate-200 hover:bg-slate-50 text-slate-700 px-3 py-1.5 rounded-lg"
          >
            Framework requirements
          </Link>
          <Link
            href="/dashboard/comply/board"
            className="inline-flex items-center gap-1.5 text-xs font-semibold bg-slate-900 hover:bg-slate-800 text-white px-3 py-1.5 rounded-lg"
          >
            Board report
            <ArrowRight size={13} />
          </Link>
          <button className="text-xs font-semibold bg-white border border-slate-200 hover:bg-slate-50 text-slate-700 px-3 py-1.5 rounded-lg">
            Export Compliance Report
          </button>
        </div>
      </div>

      {/* Framework cards */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mb-6">
        {frameworkCards.map(fw => (
          <ComplianceCoverageCard
            key={fw.key}
            framework={fw}
            gapsLabel={`${fw.gapCount} gaps remaining`}
          />
        ))}
      </div>

      {/* ISO 42001 guided journey CTA */}
      <Link
        href="/dashboard/comply/iso-42001"
        className="group flex items-center justify-between gap-4 rounded-xl border border-violet-200 bg-gradient-to-r from-violet-50 to-white p-4 mb-8 shadow-sm hover:border-violet-300 hover:shadow transition-all"
      >
        <div>
          <div className="text-[10px] font-bold uppercase tracking-wide text-violet-500">
            Guided journey
          </div>
          <h3 className="text-sm font-bold text-slate-900 mt-0.5">
            Achieve ISO/IEC 42001 compliance
          </h3>
          <p className="text-xs text-slate-500 mt-0.5 max-w-2xl leading-relaxed">
            Walk an admin through the seven AI-management-system clauses step by step — closing gaps and raising coverage toward certification-ready.
          </p>
        </div>
        <div className="shrink-0 flex flex-col items-end gap-1.5">
          <span className="inline-flex items-center gap-1.5 bg-violet-600 group-hover:bg-violet-700 text-white text-xs font-semibold px-3 py-2 rounded-lg transition-colors">
            {isoCompleted > 0 && !isoReady ? 'Resume journey →' : 'Start ISO 42001 journey →'}
            <ArrowRight size={13} />
          </span>
          {isoCompleted > 0 && (
            <span className="text-[10px] font-medium text-violet-600">
              {isoReady
                ? 'Certification-ready'
                : `${isoCompleted} of ${ISO42001_STEP_COUNT} clauses complete`}
            </span>
          )}
        </div>
      </Link>

      {/* Coverage breakdown bar */}
      <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm mb-6">
        <div className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-3">Overall Coverage Breakdown</div>
        <div className="space-y-3">
          {FRAMEWORK_METRICS.map(fw => {
            // Same source as the cards above; ISO 42001 tracks journey progress.
            const census = fw.key === 'iso42001' ? isoCensus(isoCompleted) : fw
            const { covered, partial, gap, total } = census
            return (
              <div key={fw.key}>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs font-medium text-slate-700">{fw.name}</span>
                  <span className="text-xs text-slate-400">{`${covered} covered · ${partial} partial · ${gap} gap`}</span>
                </div>
                <CoverageBar covered={covered} partial={partial} gap={gap} total={total} />
              </div>
            )
          })}
        </div>
        <div className="flex items-center gap-4 mt-3">
          {[['bg-emerald-500', 'Covered'], ['bg-amber-400', 'Partial'], ['bg-red-400', 'Gap']].map(([color, label]) => (
            <div key={label} className="flex items-center gap-1.5">
              <div className={`w-2.5 h-2.5 rounded-sm ${color}`} />
              <span className="text-xs text-slate-500">{label}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Priority Actions */}
      <div className="mb-6">
        <h2 className="text-sm font-semibold text-slate-700 mb-3">Priority Remediations</h2>
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {[
            { key: 'gdpr', fw: 'GDPR', urgency: 'critical', urgencyLabel: 'CRITICAL', action: 'Enforce PII redaction + lawful-basis checks on restricted-data use cases', deadline: 'Ongoing' },
            { key: 'euAiAct', fw: 'EU AI Act', urgency: 'high', urgencyLabel: 'HIGH', action: 'Assign human oversight to 8 high-risk use cases', deadline: 'Aug 2026' },
            { key: 'owaspLlm', fw: 'OWASP LLM', urgency: 'high', urgencyLabel: 'HIGH', action: 'Apply prompt injection guards on 6 customer-facing LLMs', deadline: 'Q2 2026' },
            { key: 'nistAiRmf', fw: 'NIST AI RMF', urgency: 'medium', urgencyLabel: 'MEDIUM', action: 'Document AI risk assessments for 12 unassessed use cases', deadline: 'Q3 2026' },
          ].map(item => (
            <div key={item.fw} className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
              <div className="flex items-start justify-between mb-2">
                <span className="text-xs font-bold text-slate-700">{item.fw}</span>
                <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full ${
                  item.urgency === 'critical' ? 'bg-red-100 text-red-700' :
                  item.urgency === 'high' ? 'bg-orange-100 text-orange-700' :
                  'bg-amber-100 text-amber-700'
                }`}>{item.urgencyLabel}</span>
              </div>
              <p className="text-xs text-slate-600 mb-3 leading-relaxed">{item.action}</p>
              <div className="flex items-center justify-between">
                <span className="text-[10px] text-slate-400">{`Deadline: ${item.deadline}`}</span>
                <button className="text-[10px] font-semibold text-indigo-600 hover:text-indigo-800">Assign →</button>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Gap table */}
      <div>
        <h2 className="text-sm font-semibold text-slate-700 mb-3">Compliance Gaps — Action Required</h2>
        <div className="bg-white border border-slate-200 rounded-xl shadow-sm overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-100 bg-slate-50">
                {['Use Case', 'Department', 'GDPR', 'EU AI Act', 'NIST AI RMF', 'OWASP LLM', 'Action'].map(h => (
                  <th key={h} className="text-left text-xs font-semibold text-slate-500 uppercase tracking-wide px-4 py-3">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {gapUseCases.map(uc => (
                <tr key={uc.id} className="border-b border-slate-50 hover:bg-slate-50">
                  <td className="px-4 py-3 text-sm font-medium text-slate-900">{uc.name}</td>
                  <td className="px-4 py-3 text-xs text-slate-500">{uc.department}</td>
                  {(['gdpr', 'euAiAct', 'nistAiRmf', 'owaspLlm'] as const).map(fw => {
                    const level = frameworkLevel(uc, fw)
                    return (
                      <td key={fw} className="px-4 py-3">
                        <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${LEVEL_BADGE[level]}`}>
                          {level.charAt(0).toUpperCase() + level.slice(1)}
                        </span>
                      </td>
                    )
                  })}
                  <td className="px-4 py-3">
                    <button className="text-xs font-semibold text-indigo-600 hover:text-indigo-800 transition-colors">
                      Assign remediation →
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
