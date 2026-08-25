"use client"

import { useState } from "react"
import { Card } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Progress } from "@/components/ui/progress"
import { cn } from "@/lib/utils"
import type {
  PolicyInstance,
  PolicyTemplate,
  PromotionEligibility
} from "@/lib/policy-types"
import { classOf } from "@/lib/policy-types"
import {
  checkPromotionEligibility,
  policyClassLabel,
  policyClassIcon
} from "@/lib/policy-helpers"

interface ModeToggleProps {
  instance: PolicyInstance
  template: PolicyTemplate
  onPromoteClick: () => void
  onDemote: () => void
}

/**
 * The flagship admin control: shows current mode, eligibility for the
 * other mode, and the explicit gate to promote/demote.
 *
 * Strict mode is shown as the *normal* state visually — solid border,
 * neutral colour. Guideline is dashed/muted to convey "still being
 * observed". This avoids alarm-fatigue for admins running 50+ Strict
 * policies in steady-state.
 */
export function PolicyModeToggle({
  instance,
  template,
  onPromoteClick,
  onDemote
}: ModeToggleProps) {
  const currentClass = classOf(instance.enforcementMode)
  const [confirmDemote, setConfirmDemote] = useState(false)

  if (currentClass === "guideline") {
    const eligibility = checkPromotionEligibility(instance, template)
    return (
      <Card
        className={cn(
          "p-6 border-dashed border-2",
          "bg-blue-50/30 dark:bg-blue-950/10"
        )}
      >
        <div className="flex items-start justify-between gap-6">
          <div className="flex-1">
            <div className="flex items-center gap-3 mb-2">
              <span className="text-2xl">{policyClassIcon("guideline")}</span>
              <div>
                <div className="text-xs uppercase tracking-wider text-muted-foreground font-medium">
                  Current mode
                </div>
                <div className="text-xl font-semibold">
                  {policyClassLabel("guideline")}
                </div>
              </div>
            </div>
            <p className="text-sm text-muted-foreground max-w-xl">
              This policy is observing traffic and recording would-block
              events. Nothing is being altered or blocked. Use this period to
              measure false-positive rate before promoting to Strict.
            </p>
          </div>

          <div className="text-right shrink-0">
            <button
              onClick={onPromoteClick}
              disabled={!eligibility.eligible}
              className={cn(
                "px-4 py-2 rounded-md text-sm font-medium transition-colors",
                eligibility.eligible
                  ? "bg-foreground text-background hover:opacity-90"
                  : "bg-muted text-muted-foreground cursor-not-allowed"
              )}
            >
              {eligibility.eligible ? "Promote to Strict →" : "Not yet eligible"}
            </button>
          </div>
        </div>

        <PromotionChecklist eligibility={eligibility} />
      </Card>
    )
  }

  // Strict mode
  return (
    <Card className="p-6 border-2">
      <div className="flex items-start justify-between gap-6">
        <div className="flex-1">
          <div className="flex items-center gap-3 mb-2">
            <span className="text-2xl">{policyClassIcon("strict")}</span>
            <div>
              <div className="text-xs uppercase tracking-wider text-muted-foreground font-medium">
                Current mode
              </div>
              <div className="text-xl font-semibold flex items-center gap-2">
                {policyClassLabel("strict")}
                <Badge variant="success" className="font-mono text-[10px]">
                  ● LIVE
                </Badge>
              </div>
            </div>
          </div>
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm text-muted-foreground">
            <span>
              {instance.stats?.blockCount30d ?? 0} blocks / 30d
            </span>
            <span>
              FP rate: {((instance.stats?.falsePositiveRate ?? 0) * 100).toFixed(1)}%
            </span>
            <span>Rollout: {instance.rolloutStrategy}</span>
          </div>
        </div>

        <div className="text-right shrink-0">
          {!confirmDemote ? (
            <button
              onClick={() => setConfirmDemote(true)}
              className="px-4 py-2 rounded-md text-sm font-medium border border-input hover:bg-accent transition-colors"
            >
              ⏸ Pause to Guideline
            </button>
          ) : (
            <div className="flex gap-2">
              <button
                onClick={() => setConfirmDemote(false)}
                className="px-3 py-2 rounded-md text-sm border border-input hover:bg-accent"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  onDemote()
                  setConfirmDemote(false)
                }}
                className="px-3 py-2 rounded-md text-sm font-medium bg-destructive text-white hover:opacity-90"
              >
                Confirm demote
              </button>
            </div>
          )}
        </div>
      </div>
    </Card>
  )
}

// ─── Promotion checklist (sub-component) ─────────────────────────────

function PromotionChecklist({ eligibility }: { eligibility: PromotionEligibility }) {
  const checks = [
    {
      label: `${eligibility.daysRequired}+ days in Guideline mode`,
      detail: `currently ${eligibility.daysInGuideline} day(s)`,
      passed: eligibility.daysInGuideline >= eligibility.daysRequired,
      progress: Math.min(
        100,
        (eligibility.daysInGuideline / eligibility.daysRequired) * 100
      )
    },
    {
      label: `False-positive rate below ${(eligibility.fpRateThreshold * 100).toFixed(1)}%`,
      detail: `currently ${(eligibility.falsePositiveRate * 100).toFixed(1)}%`,
      passed: eligibility.falsePositiveRate <= eligibility.fpRateThreshold,
      progress: 100 // binary
    }
  ]

  return (
    <div className="mt-6 pt-6 border-t space-y-3">
      <div className="text-xs uppercase tracking-wider text-muted-foreground font-medium">
        Promotion eligibility
      </div>

      {checks.map((c, i) => (
        <div key={i} className="flex items-start gap-3">
          <span
            className={cn(
              "mt-0.5 size-5 rounded-full inline-flex items-center justify-center text-xs",
              c.passed
                ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300"
                : "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300"
            )}
            aria-hidden
          >
            {c.passed ? "✓" : "⋯"}
          </span>
          <div className="flex-1">
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-sm">{c.label}</span>
              <span className="text-xs text-muted-foreground font-mono">
                {c.detail}
              </span>
            </div>
            {!c.passed && c.progress < 100 && (
              <Progress value={c.progress} className="h-1 mt-1" />
            )}
          </div>
        </div>
      ))}

      {eligibility.approvers.map((a, i) => (
        <div key={`approver-${i}`} className="flex items-start gap-3">
          <span
            className={cn(
              "mt-0.5 size-5 rounded-full inline-flex items-center justify-center text-xs",
              a.status === "approved"
                ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300"
                : a.status === "rejected"
                  ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300"
                  : "bg-muted text-muted-foreground"
            )}
            aria-hidden
          >
            {a.status === "approved" ? "✓" : a.status === "rejected" ? "✗" : "○"}
          </span>
          <div className="flex-1">
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-sm">Approval: {a.role}</span>
              <span className="text-xs text-muted-foreground capitalize">
                {a.status}
              </span>
            </div>
          </div>
        </div>
      ))}

      {eligibility.blockingReasons.length > 0 && (
        <div className="mt-4 p-3 rounded-md bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-900">
          <div className="text-xs font-semibold text-amber-900 dark:text-amber-200 mb-1">
            Why you can&apos;t promote yet
          </div>
          <ul className="text-xs text-amber-800 dark:text-amber-300 space-y-1 list-disc list-inside">
            {eligibility.blockingReasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
