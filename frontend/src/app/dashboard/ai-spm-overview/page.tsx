// AI-SPM Overview — persona-keyed dashboard ported from
// security-platform-demo's customer-feedback-driven redesign.
//
// What the user sees:
//   1. Top strip: 4 adoption KPIs (Overall Adoption, Trained
//      Workforce, Business Units Enabled, Use Cases in Production).
//      These don't change when persona switches.
//   2. Persona selector tabs: Executive / Finance / Security /
//      Technology / Compliance. Picks 4 metrics relevant to that role.
//   3. Each metric card shows: title, the question it answers (italic
//      subtitle), the live value, the trend (with isPositive colour),
//      and — most importantly per customer feedback — the data source
//      (Azure ML, Purview DLP, Compliance Manager, etc.) so anyone
//      can verify provenance.
//   4. Click-through to the relevant detail page where one exists.
//
// Today: values are pinned strings. When cursor's M2 lands, swap the
// hard-coded `currentValue` for an async loader against the relevant
// router under `/api/v1/`.

"use client"

import { useState } from "react"
import { Card } from "@/components/ui/card"
import { AdoptionKpiStrip } from "@/components/spm/adoption-kpi-strip"
import { PersonaMetricCard } from "@/components/spm/persona-metric-card"
import { PersonaSelector } from "@/components/spm/persona-selector"
import {
  ADOPTION_KPIS,
  PERSONAS,
  metricsForPersona,
  type PersonaId,
} from "@/lib/persona-metrics"

export default function AISPMOverviewPage() {
  const [persona, setPersona] = useState<PersonaId>("executive")
  const active = PERSONAS.find((p) => p.id === persona)!
  const metrics = metricsForPersona(persona)

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">AI-SPM Overview</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Persona-keyed view of AI adoption, security, and governance posture.
          Every metric shows its data source.
        </p>
      </div>

      {/* Top KPI strip */}
      <AdoptionKpiStrip kpis={ADOPTION_KPIS} />

      {/* Persona tabs */}
      <div className="space-y-3 pt-2 border-t">
        <div className="flex flex-col gap-2">
          <PersonaSelector active={persona} onChange={setPersona} />
          <p className="text-xs text-muted-foreground italic">
            Showing as <span className="font-medium not-italic">{active.label}</span> ·{" "}
            {active.description}
          </p>
        </div>
      </div>

      {/* Persona metrics */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
        {metrics.map((m) => (
          <PersonaMetricCard key={m.id} metric={m} />
        ))}
      </div>

      {/* Footnote on data sources */}
      <Card className="p-4 bg-muted/30 border-dashed">
        <p className="text-xs text-muted-foreground">
          <strong className="text-foreground">Data provenance.</strong>{" "}
          Every metric on this page declares its data source explicitly.
          When a number looks off, click the source name to navigate to the
          underlying system. This is by design: customer feedback during the
          security-platform-demo prototype was that metrics without a
          traceable origin become &quot;trust me&quot; numbers — not actionable data.
          We list the source on every card.
        </p>
      </Card>
    </div>
  )
}
