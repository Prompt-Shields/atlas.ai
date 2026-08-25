// PersonaMetricCard — the building block of the AI-SPM overview page.
//
// Customer feedback that drove this design (security-platform-demo):
//   1. The exec asks "where did this number come from?"
//      → Every card displays the dataSource verbatim, not buried in
//        a tooltip.
//   2. The exec asks "what question does this answer?"
//      → The questionAnswered line is rendered as an italic subtitle.
//   3. The exec asks "is the trend good or bad?"
//      → trend.isPositive controls colour: green when the change
//        improves the metric, red when it degrades. (Down can be good
//        for risk metrics, so we don't infer from the direction.)

"use client"

import Link from "next/link"
import { ArrowDown, ArrowRight, ArrowUp, Database, Minus } from "lucide-react"
import { Card } from "@/components/ui/card"
import { cn } from "@/lib/utils"
import type { PersonaMetric } from "@/lib/persona-metrics"

const TONE_CLASSES: Record<PersonaMetric["tone"], string> = {
  ok: "text-emerald-700",
  neutral: "text-foreground",
  warn: "text-amber-700",
  alert: "text-rose-700",
}

const TONE_BORDERS: Record<PersonaMetric["tone"], string> = {
  ok: "border-emerald-200/60",
  neutral: "border-input",
  warn: "border-amber-200",
  alert: "border-rose-200",
}

interface Props {
  metric: PersonaMetric
}

export function PersonaMetricCard({ metric }: Props) {
  const isInteractive = metric.drillDownHref !== ""

  const inner = (
    <Card
      className={cn(
        "p-4 h-full flex flex-col gap-3 transition-shadow border",
        TONE_BORDERS[metric.tone],
        isInteractive && "group-hover:shadow-md cursor-pointer",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold leading-snug">{metric.title}</h3>
          <p className="text-xs italic text-muted-foreground mt-0.5">
            {metric.questionAnswered}
          </p>
        </div>
        {isInteractive && (
          <ArrowRight
            size={14}
            className="text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity shrink-0 mt-1"
          />
        )}
      </div>

      <div className="flex items-baseline gap-3">
        <span className={cn("text-2xl font-semibold tabular-nums", TONE_CLASSES[metric.tone])}>
          {metric.currentValue}
        </span>
        {metric.trend && <TrendBadge trend={metric.trend} />}
      </div>

      <div className="mt-auto pt-2 border-t flex items-center gap-1.5 text-[11px] text-muted-foreground">
        <Database size={11} className="shrink-0" />
        <span className="truncate" title={metric.dataSource}>
          Source: {metric.dataSource}
        </span>
      </div>
    </Card>
  )

  if (!isInteractive) return inner
  return (
    <Link href={metric.drillDownHref} className="group block h-full">
      {inner}
    </Link>
  )
}

function TrendBadge({ trend }: { trend: NonNullable<PersonaMetric["trend"]> }) {
  const Icon = trend.direction === "up" ? ArrowUp : trend.direction === "down" ? ArrowDown : Minus
  return (
    <span
      className={cn(
        "inline-flex items-center gap-0.5 text-xs font-medium",
        trend.isPositive ? "text-emerald-600" : "text-rose-600",
      )}
      title={`${trend.direction} ${trend.deltaPct ?? 0}% (30d)`}
    >
      <Icon size={12} />
      {trend.deltaPct !== undefined && <span>{trend.deltaPct}%</span>}
    </span>
  )
}
