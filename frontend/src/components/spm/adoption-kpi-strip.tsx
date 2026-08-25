// Top-of-page strip showing the four "are we winning at AI" KPIs.
// Persona-agnostic — every viewer sees the same four numbers as
// context for their persona-specific drill-down.
//
// Customer feedback (security-platform-demo): "Don't make the CFO scroll
// past the CISO's metrics to see adoption". So this strip lives ABOVE
// the persona selector.

import { Card } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { cn } from "@/lib/utils"
import type { AdoptionKpi } from "@/lib/persona-metrics"

const ACCENT_BAR: Record<AdoptionKpi["accent"], string> = {
  indigo: "bg-indigo-500",
  emerald: "bg-emerald-500",
  amber: "bg-amber-500",
  violet: "bg-violet-500",
}

const ACCENT_TEXT: Record<AdoptionKpi["accent"], string> = {
  indigo: "text-indigo-700",
  emerald: "text-emerald-700",
  amber: "text-amber-700",
  violet: "text-violet-700",
}

export function AdoptionKpiStrip({ kpis }: { kpis: AdoptionKpi[] }) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      {kpis.map((k) => (
        <Card key={k.id} className="p-4 flex flex-col gap-2">
          <div>
            <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              {k.title}
            </h3>
            <p className="text-[11px] text-muted-foreground mt-0.5 leading-tight">
              {k.subtitle}
            </p>
          </div>
          <div className="flex items-baseline justify-between">
            <span className={cn("text-2xl font-semibold tabular-nums", ACCENT_TEXT[k.accent])}>
              {k.value}
            </span>
            <span className="text-[10px] text-muted-foreground">{k.percentage}%</span>
          </div>
          <Progress
            value={k.percentage}
            className={cn("h-1.5 [&>div]:bg-transparent", "[&>div]:" + ACCENT_BAR[k.accent])}
          />
          {/* The Progress primitive renders a <div> indicator child; we
              tint it via the [&>div]: selector. Tailwind needs the
              explicit colour class somewhere reachable, so we duplicate
              the constant inline as a className string. */}
          <div className={cn("hidden", ACCENT_BAR[k.accent])} aria-hidden />
        </Card>
      ))}
    </div>
  )
}
