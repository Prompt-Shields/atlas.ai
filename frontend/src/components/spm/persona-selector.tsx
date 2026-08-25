// Persona selector — segmented control that lets a user view the
// dashboard "as" Executive / Finance / Security / Technology /
// Compliance.

"use client"

import { cn } from "@/lib/utils"
import { PERSONAS, type PersonaId } from "@/lib/persona-metrics"

const ACCENT_ACTIVE: Record<string, string> = {
  indigo: "bg-indigo-100 text-indigo-900 border-indigo-300",
  amber: "bg-amber-100 text-amber-900 border-amber-300",
  rose: "bg-rose-100 text-rose-900 border-rose-300",
  sky: "bg-sky-100 text-sky-900 border-sky-300",
  emerald: "bg-emerald-100 text-emerald-900 border-emerald-300",
}

interface Props {
  active: PersonaId
  onChange: (id: PersonaId) => void
}

export function PersonaSelector({ active, onChange }: Props) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {PERSONAS.map((p) => {
        const isActive = p.id === active
        return (
          <button
            key={p.id}
            onClick={() => onChange(p.id)}
            className={cn(
              "px-3 py-1.5 rounded-md border text-sm transition-colors",
              isActive
                ? ACCENT_ACTIVE[p.accent]
                : "border-input hover:bg-accent text-muted-foreground",
            )}
          >
            <span className="font-medium">{p.short}</span>
            {isActive && (
              <span className="ml-2 text-xs opacity-70 hidden md:inline">
                · {p.label.replace(p.short, "").replace(/^[\s(]+|[\s)]+$/g, "")}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}
