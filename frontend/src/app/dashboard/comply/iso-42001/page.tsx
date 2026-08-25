'use client'

// /dashboard/comply/iso-42001 — guided, step-by-step admin journey to
// ISO/IEC 42001 compliance, used as a demo example (PRO-52). Walks the
// seven management-system clauses (4–10); each completed clause raises the
// framework coverage ring from the baseline toward a "certification-ready"
// 100%. Progress persists to localStorage so the /dashboard/comply overview
// reflects the climbed score.

import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import {
  ArrowLeft,
  Check,
  Lock,
  RotateCcw,
  FileCheck2,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'
import {
  ISO42001_STEPS,
  ISO42001_STEP_COUNT,
  ISO42001_COLOR,
  ISO42001_BASE_COVERAGE,
  computeCoverage,
  computeGapCount,
  isCertificationReady,
  readCompletedSteps,
  writeCompletedSteps,
} from '@/lib/aispm/iso42001-journey'

// ── Animated number (tweens between values for the theatrical climb) ──

function useAnimatedNumber(target: number, durationMs = 700): number {
  const [value, setValue] = useState(target)
  const fromRef = useRef(target)
  const rafRef = useRef<number | null>(null)
  const startRef = useRef<number | null>(null)

  useEffect(() => {
    const from = fromRef.current
    if (from === target) return
    startRef.current = null

    const tick = (now: number) => {
      if (startRef.current === null) startRef.current = now
      const elapsed = now - startRef.current
      const t = Math.min(elapsed / durationMs, 1)
      // easeOutCubic
      const eased = 1 - Math.pow(1 - t, 3)
      setValue(Math.round(from + (target - from) * eased))
      if (t < 1) {
        rafRef.current = requestAnimationFrame(tick)
      } else {
        fromRef.current = target
      }
    }

    rafRef.current = requestAnimationFrame(tick)
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current)
      fromRef.current = target
    }
  }, [target, durationMs])

  return value
}

// ── Coverage ring ─────────────────────────────────────────────────────

function CoverageRing({ percentage }: { percentage: number }) {
  const animated = useAnimatedNumber(percentage)
  const size = 132
  const stroke = 12
  const r = (size - stroke) / 2
  const circumference = 2 * Math.PI * r
  const offset = circumference * (1 - animated / 100)

  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="#e2e8f0"
          strokeWidth={stroke}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={ISO42001_COLOR}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          style={{ transition: 'stroke-dashoffset 0.1s linear' }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-3xl font-bold tabular-nums" style={{ color: ISO42001_COLOR }}>
          {animated}%
        </span>
        <span className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">
          coverage
        </span>
      </div>
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────

export default function Iso42001JourneyPage() {
  // `null` until hydrated from localStorage (avoids SSR/client mismatch).
  const [completed, setCompleted] = useState<string[] | null>(null)

  useEffect(() => {
    setCompleted(readCompletedSteps())
  }, [])

  const completedSet = new Set(completed ?? [])
  const completedCount = completed?.length ?? 0
  const coverage = computeCoverage(completedCount)
  const gaps = computeGapCount(completedCount)
  const ready = isCertificationReady(completedCount)

  // Index of the next actionable (first incomplete) step.
  const nextIndex = ISO42001_STEPS.findIndex((s) => !completedSet.has(s.id))

  function completeStep(id: string) {
    if (!completed) return
    if (completedSet.has(id)) return
    const next = [...completed, id]
    setCompleted(next)
    writeCompletedSteps(next)
  }

  function reset() {
    setCompleted([])
    writeCompletedSteps([])
  }

  return (
    <div>
      {/* Breadcrumb / back */}
      <Link
        href="/dashboard/comply"
        className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-500 hover:text-slate-700 mb-4"
      >
        <ArrowLeft size={14} />
        Back to Compliance
      </Link>

      {/* Header */}
      <div className="flex items-start justify-between gap-4 mb-6">
        <div>
          <div className="flex items-center gap-2">
            <ShieldCheck size={20} style={{ color: ISO42001_COLOR }} />
            <h1 className="text-xl font-bold text-slate-900">
              ISO/IEC 42001 compliance journey
            </h1>
          </div>
          <p className="text-sm text-slate-500 mt-0.5 max-w-2xl">
            A guided, step-by-step path to a certification-ready AI management
            system (AIMS). Complete each clause to close gaps and raise your
            coverage. Illustrative demo data.
          </p>
        </div>
        <button
          onClick={reset}
          className="shrink-0 inline-flex items-center gap-1.5 text-xs font-semibold bg-white border border-slate-200 hover:bg-slate-50 text-slate-600 px-3 py-1.5 rounded-lg"
        >
          <RotateCcw size={13} />
          Reset journey
        </button>
      </div>

      {/* Progress hero */}
      <div
        className={`rounded-2xl border p-6 mb-8 shadow-sm transition-colors ${
          ready
            ? 'border-violet-200 bg-gradient-to-br from-violet-50 to-white'
            : 'border-slate-200 bg-white'
        }`}
      >
        <div className="flex items-center gap-6">
          <CoverageRing percentage={coverage} />
          <div className="flex-1">
            {ready ? (
              <div className="flex items-center gap-2 mb-1">
                <Sparkles size={18} className="text-violet-500" />
                <span className="text-lg font-bold text-violet-700">
                  Certification-ready
                </span>
              </div>
            ) : (
              <span className="text-lg font-bold text-slate-900">
                {completedCount === 0 ? 'Not started' : 'In progress'}
              </span>
            )}
            <p className="text-sm text-slate-500 mt-0.5">
              {ready
                ? 'All seven management clauses are in place. Your AIMS is ready for a Stage 2 certification audit.'
                : `${completedCount} of ${ISO42001_STEP_COUNT} clauses complete · ${gaps} gaps remaining · started at ${ISO42001_BASE_COVERAGE}%`}
            </p>

            {/* Segment progress */}
            <div className="flex items-center gap-1.5 mt-4">
              {ISO42001_STEPS.map((s, i) => (
                <div
                  key={s.id}
                  className="h-1.5 flex-1 rounded-full transition-colors duration-300"
                  style={{
                    backgroundColor: completedSet.has(s.id)
                      ? ISO42001_COLOR
                      : i === nextIndex
                        ? '#ddd6fe'
                        : '#f1f5f9',
                  }}
                />
              ))}
            </div>

            {ready && (
              <button
                onClick={() => alert('Evidence pack exported (demo).')}
                className="mt-5 inline-flex items-center gap-2 bg-violet-600 hover:bg-violet-700 text-white text-sm font-semibold px-4 py-2 rounded-xl transition-colors"
              >
                <FileCheck2 size={15} />
                Export evidence pack
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Steps */}
      <ol className="space-y-3">
        {ISO42001_STEPS.map((step, i) => {
          const done = completedSet.has(step.id)
          const isNext = i === nextIndex
          const locked = !done && !isNext

          return (
            <li
              key={step.id}
              className={`rounded-xl border p-5 shadow-sm transition-all ${
                done
                  ? 'border-violet-100 bg-violet-50/40'
                  : isNext
                    ? 'border-violet-300 bg-white ring-1 ring-violet-100'
                    : 'border-slate-200 bg-white opacity-60'
              }`}
            >
              <div className="flex items-start gap-4">
                {/* Status marker */}
                <div
                  className={`shrink-0 flex items-center justify-center w-9 h-9 rounded-full text-sm font-bold ${
                    done
                      ? 'bg-violet-600 text-white'
                      : isNext
                        ? 'bg-violet-100 text-violet-700'
                        : 'bg-slate-100 text-slate-400'
                  }`}
                >
                  {done ? <Check size={16} /> : locked ? <Lock size={14} /> : i + 1}
                </div>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-[10px] font-bold uppercase tracking-wide text-violet-500">
                      {step.clause}
                    </span>
                    <h3 className="text-sm font-bold text-slate-900">
                      {step.clauseTitle}
                    </h3>
                  </div>
                  <p className="text-sm text-slate-600 leading-relaxed mt-1.5">
                    {step.action}
                  </p>

                  {/* Artifact produced */}
                  <div className="mt-3 flex items-start gap-1.5 text-xs">
                    <FileCheck2 size={13} className="text-slate-400 mt-0.5 shrink-0" />
                    <span className="text-slate-500">
                      Produces:{' '}
                      <span className="font-medium text-slate-700">
                        {step.artifact}
                      </span>
                    </span>
                  </div>

                  {/* Annex A controls */}
                  {step.annexControls.length > 0 && (
                    <div className="mt-2 flex items-center gap-1.5 flex-wrap">
                      {step.annexControls.map((c) => (
                        <span
                          key={c.id}
                          title={c.name}
                          className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-violet-100 text-violet-700"
                        >
                          {c.id} · {c.name}
                        </span>
                      ))}
                    </div>
                  )}

                  {/* CTA */}
                  <div className="mt-4">
                    {done ? (
                      <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-violet-700">
                        <Check size={13} /> Completed
                      </span>
                    ) : (
                      <button
                        onClick={() => completeStep(step.id)}
                        disabled={locked || completed === null}
                        className={`text-sm font-semibold px-4 py-2 rounded-xl transition-colors ${
                          locked || completed === null
                            ? 'bg-slate-100 text-slate-400 cursor-not-allowed'
                            : 'bg-violet-600 hover:bg-violet-700 text-white'
                        }`}
                      >
                        {locked ? 'Complete previous clause first' : 'Mark complete'}
                      </button>
                    )}
                  </div>
                </div>
              </div>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
