'use client'

// /dashboard/comply/frameworks — framework requirements inventory
// (cross-framework crosswalk). What each AI-governance framework requires on
// the core governance topics (data, ownership, risk, oversight, …), at
// article/clause granularity. Filter by framework to see everything one
// framework asks for, or read a topic to see how one control satisfies several
// frameworks at once.
//
// Ported from ai-spm-dashboard/app/comply/frameworks/page.tsx.

import { useMemo, useState } from 'react'
import Link from 'next/link'
import { ArrowLeft, Search, Layers } from 'lucide-react'
import {
  REQUIREMENT_FRAMEWORKS,
  REQUIREMENT_TOPICS,
  type RequirementFrameworkKey,
} from '@/lib/aispm/framework-requirements'

export default function FrameworkRequirementsPage() {
  const allKeys = REQUIREMENT_FRAMEWORKS.map((f) => f.key)
  const [selected, setSelected] = useState<Set<RequirementFrameworkKey>>(new Set(allKeys))
  const [query, setQuery] = useState('')

  function toggle(key: RequirementFrameworkKey) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      // Never allow zero selected — reset to all.
      return next.size === 0 ? new Set(allKeys) : next
    })
  }

  const colorOf = (key: RequirementFrameworkKey) =>
    REQUIREMENT_FRAMEWORKS.find((f) => f.key === key)!.color

  const topics = useMemo(() => {
    const q = query.trim().toLowerCase()
    return REQUIREMENT_TOPICS.map((t) => ({
      ...t,
      refs: t.refs.filter((r) => selected.has(r.fw)),
    }))
      .filter((t) => t.refs.length > 0)
      .filter((t) => {
        if (!q) return true
        const hay = (
          t.topic +
          ' ' +
          t.summary +
          ' ' +
          t.refs.map((r) => r.note + ' ' + r.clause).join(' ')
        ).toLowerCase()
        return hay.includes(q)
      })
  }, [selected, query])

  return (
    <div className="max-w-5xl">
      {/* Breadcrumb */}
      <Link
        href="/dashboard/comply"
        className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-500 hover:text-slate-700 mb-4"
      >
        <ArrowLeft size={14} />
        Back to Compliance
      </Link>

      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center gap-2">
          <Layers size={20} className="text-indigo-500" />
          <h1 className="text-xl font-bold text-slate-900">Framework requirements inventory</h1>
        </div>
        <p className="text-sm text-slate-500 mt-1 max-w-3xl">
          What each framework requires on the core governance topics — data, ownership, risk,
          oversight and more — at article/clause level. One control, evidenced once, can satisfy
          several frameworks at the same time. Illustrative reference.
        </p>
      </div>

      {/* Controls */}
      <div className="flex flex-wrap items-center gap-3 mb-6">
        <div className="relative">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search requirements…"
            className="w-64 rounded-lg border border-slate-200 pl-8 pr-3 py-1.5 text-sm text-slate-900 focus:border-indigo-400 focus:outline-none focus:ring-1 focus:ring-indigo-200"
          />
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {REQUIREMENT_FRAMEWORKS.map((f) => {
            const on = selected.has(f.key)
            return (
              <button
                key={f.key}
                onClick={() => toggle(f.key)}
                className={`text-xs font-semibold px-2.5 py-1 rounded-full border transition-colors ${
                  on ? 'text-white' : 'text-slate-500 bg-white border-slate-200 hover:bg-slate-50'
                }`}
                style={on ? { backgroundColor: f.color, borderColor: f.color } : undefined}
              >
                {f.label}
              </button>
            )
          })}
        </div>
      </div>

      {/* Topics */}
      <p className="text-xs text-slate-400 mb-3">
        {topics.length} {topics.length === 1 ? 'topic' : 'topics'}
      </p>
      <div className="space-y-4">
        {topics.map((t) => (
          <div key={t.id} className="rounded-xl border border-slate-200 bg-white shadow-sm p-5">
            <h2 className="text-sm font-bold text-slate-900">{t.topic}</h2>
            <p className="text-sm text-slate-600 leading-relaxed mt-1 mb-4">{t.summary}</p>
            <div className="grid gap-x-6 gap-y-3 md:grid-cols-2">
              {t.refs.map((r) => (
                <div key={`${t.id}-${r.fw}`} className="flex items-start gap-2.5">
                  <span
                    className="shrink-0 mt-0.5 text-[10px] font-semibold text-white px-2 py-0.5 rounded"
                    style={{ backgroundColor: colorOf(r.fw) }}
                  >
                    {REQUIREMENT_FRAMEWORKS.find((f) => f.key === r.fw)!.label}
                  </span>
                  <div className="min-w-0">
                    <p className="text-xs font-semibold text-slate-700">{r.clause}</p>
                    <p className="text-xs text-slate-500 leading-relaxed">{r.note}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
        {topics.length === 0 && (
          <p className="text-sm italic text-slate-500 py-8 text-center">
            No requirements match your search.
          </p>
        )}
      </div>
    </div>
  )
}
