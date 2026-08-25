"use client"

import { useMemo, useState } from "react"
import { Card } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Progress } from "@/components/ui/progress"
import { cn } from "@/lib/utils"
import type {
  PolicyInstance,
  PolicyTemplate,
  ApprovalRecord,
  RolloutStrategy
} from "@/lib/policy-types"
import {
  buildRiskPreview,
  requiredApproversFor,
  policyClassIcon
} from "@/lib/policy-helpers"

interface PromotionWizardProps {
  instance: PolicyInstance
  template: PolicyTemplate
  applications: Array<{ id: string; name: string }>
  detectorBreakdown?: Record<string, number>
  onConfirm: (params: {
    approvers: ApprovalRecord[]
    rolloutStrategy: RolloutStrategy
    rolloutCanaryAppId?: string
    rolloutPercentage?: number
    autoRevertOnHighFp: boolean
    autoRevertThreshold: number
  }) => void
  onCancel: () => void
}

type Step = 1 | 2 | 3 | 4

/**
 * Four-step wizard for promoting Guideline → Strict.
 *  1. Risk preview — show blast radius from last 30d of Guideline data
 *  2. Rollout strategy — all / canary / phased
 *  3. Override path — reviewer roles, auto-revert thresholds
 *  4. Approval — list required signers and submit
 */
export function PromotionWizard({
  instance,
  template,
  applications,
  detectorBreakdown,
  onConfirm,
  onCancel
}: PromotionWizardProps) {
  const [step, setStep] = useState<Step>(1)

  // Step 2 state
  const [strategy, setStrategy] = useState<RolloutStrategy>("canary")
  const [canaryAppId, setCanaryAppId] = useState<string | undefined>(
    applications[0]?.id
  )
  const [phasedPct, setPhasedPct] = useState(10)

  // Step 3 state
  const [autoRevert, setAutoRevert] = useState(true)
  const [autoRevertThreshold, setAutoRevertThreshold] = useState(5)

  const requiredApprovers = useMemo(() => requiredApproversFor(template), [template])
  const risk = useMemo(
    () => buildRiskPreview(instance, detectorBreakdown),
    [instance, detectorBreakdown]
  )

  const handleSubmit = () => {
    const approvers: ApprovalRecord[] = requiredApprovers.map((role) => ({
      role,
      status: "pending"
    }))
    onConfirm({
      approvers,
      rolloutStrategy: strategy,
      rolloutCanaryAppId: strategy === "canary" ? canaryAppId : undefined,
      rolloutPercentage: strategy === "phased" ? phasedPct : undefined,
      autoRevertOnHighFp: autoRevert,
      autoRevertThreshold
    })
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4">
      <Card className="w-full max-w-2xl max-h-[90vh] overflow-y-auto p-0">
        <div className="px-6 py-4 border-b flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-xl">{policyClassIcon("strict")}</span>
            <div>
              <div className="text-xs uppercase tracking-wider text-muted-foreground">
                Promote to Strict
              </div>
              <div className="text-base font-semibold">{instance.name}</div>
            </div>
          </div>
          <button
            onClick={onCancel}
            className="text-muted-foreground hover:text-foreground text-xl leading-none"
            aria-label="Cancel"
          >
            ✕
          </button>
        </div>

        <StepIndicator current={step} />

        <div className="px-6 py-6">
          {step === 1 && <Step1RiskPreview risk={risk} />}
          {step === 2 && (
            <Step2Rollout
              strategy={strategy}
              setStrategy={setStrategy}
              applications={applications}
              canaryAppId={canaryAppId}
              setCanaryAppId={setCanaryAppId}
              phasedPct={phasedPct}
              setPhasedPct={setPhasedPct}
            />
          )}
          {step === 3 && (
            <Step3Override
              autoRevert={autoRevert}
              setAutoRevert={setAutoRevert}
              threshold={autoRevertThreshold}
              setThreshold={setAutoRevertThreshold}
            />
          )}
          {step === 4 && (
            <Step4Approval
              approvers={requiredApprovers}
              strategy={strategy}
              instance={instance}
            />
          )}
        </div>

        <div className="px-6 py-4 border-t flex items-center justify-between bg-muted/30">
          <button
            onClick={() => (step > 1 ? setStep((step - 1) as Step) : onCancel())}
            className="text-sm px-3 py-1.5 rounded-md border border-input hover:bg-accent"
          >
            {step > 1 ? "← Back" : "Cancel"}
          </button>
          {step < 4 ? (
            <button
              onClick={() => setStep((step + 1) as Step)}
              className="text-sm px-4 py-1.5 rounded-md font-medium bg-foreground text-background hover:opacity-90"
            >
              Next →
            </button>
          ) : (
            <button
              onClick={handleSubmit}
              className="text-sm px-4 py-1.5 rounded-md font-medium bg-foreground text-background hover:opacity-90"
            >
              Send for approval
            </button>
          )}
        </div>
      </Card>
    </div>
  )
}

// ─── Step indicator ──────────────────────────────────────────────────

function StepIndicator({ current }: { current: Step }) {
  const steps = [
    { n: 1, label: "Risk preview" },
    { n: 2, label: "Rollout" },
    { n: 3, label: "Override path" },
    { n: 4, label: "Approval" }
  ]
  return (
    <div className="px-6 py-3 border-b bg-muted/20">
      <div className="flex items-center gap-2">
        {steps.map((s, i) => (
          <div key={s.n} className="flex items-center gap-2 flex-1">
            <div
              className={cn(
                "size-6 rounded-full flex items-center justify-center text-xs font-semibold",
                s.n === current
                  ? "bg-foreground text-background"
                  : s.n < current
                    ? "bg-green-500 text-white"
                    : "bg-muted text-muted-foreground"
              )}
            >
              {s.n < current ? "✓" : s.n}
            </div>
            <span
              className={cn(
                "text-xs",
                s.n === current ? "font-semibold" : "text-muted-foreground"
              )}
            >
              {s.label}
            </span>
            {i < steps.length - 1 && (
              <div className="flex-1 h-px bg-border" aria-hidden />
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

// ─── Step 1: Risk preview ────────────────────────────────────────────

function Step1RiskPreview({ risk }: { risk: ReturnType<typeof buildRiskPreview> }) {
  return (
    <div className="space-y-5">
      <div>
        <h3 className="text-base font-semibold mb-1">Blast radius preview</h3>
        <p className="text-sm text-muted-foreground">
          Based on the last 30 days while this policy was in Guideline mode.
          Switching to Strict will start blocking matching events from now on.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <Stat label="Total evaluations" value={risk.totalEvaluations.toLocaleString()} />
        <Stat
          label="Would-block events"
          value={`${risk.wouldBlockCount.toLocaleString()}`}
          sub={`${risk.wouldBlockPercentage.toFixed(2)}% of traffic`}
        />
        <Stat
          label="Estimated false positives"
          value={risk.estimatedFalsePositives.toLocaleString()}
          tone="warning"
        />
        <Stat
          label="Affected users (est.)"
          value={risk.estimatedAffectedUsers.toLocaleString()}
          tone="warning"
        />
      </div>

      {risk.topReasons.length > 0 && (
        <div>
          <div className="text-xs uppercase tracking-wider text-muted-foreground font-medium mb-2">
            Top would-block reasons
          </div>
          <div className="space-y-2">
            {risk.topReasons.map((r) => (
              <div key={r.reason}>
                <div className="flex items-baseline justify-between gap-3 text-sm mb-1">
                  <span className="font-medium">{r.reason}</span>
                  <span className="text-muted-foreground font-mono text-xs">
                    {r.count} ({r.pct.toFixed(1)}%)
                  </span>
                </div>
                <Progress value={r.pct} className="h-1.5" />
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function Stat({
  label,
  value,
  sub,
  tone
}: {
  label: string
  value: string
  sub?: string
  tone?: "warning" | "default"
}) {
  return (
    <div
      className={cn(
        "rounded-md border p-3",
        tone === "warning" && "bg-amber-50/50 dark:bg-amber-950/10 border-amber-200/50 dark:border-amber-900/30"
      )}
    >
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-2xl font-semibold tabular-nums mt-0.5">{value}</div>
      {sub && <div className="text-xs text-muted-foreground mt-0.5">{sub}</div>}
    </div>
  )
}

// ─── Step 2: Rollout strategy ────────────────────────────────────────

function Step2Rollout({
  strategy,
  setStrategy,
  applications,
  canaryAppId,
  setCanaryAppId,
  phasedPct,
  setPhasedPct
}: {
  strategy: RolloutStrategy
  setStrategy: (s: RolloutStrategy) => void
  applications: Array<{ id: string; name: string }>
  canaryAppId: string | undefined
  setCanaryAppId: (id: string) => void
  phasedPct: number
  setPhasedPct: (n: number) => void
}) {
  const options: Array<{ value: RolloutStrategy; label: string; detail: string }> = [
    {
      value: "all",
      label: "All applications immediately",
      detail: `Activate Strict on all ${applications.length} app(s) at once. Highest risk, highest leverage.`
    },
    {
      value: "canary",
      label: "Canary — start with one app",
      detail: "Pick one application to enforce on first. If stable for 7 days, expand."
    },
    {
      value: "phased",
      label: "Phased rollout",
      detail: "Gradually increase coverage from N% to 100% over a configurable window."
    }
  ]
  return (
    <div className="space-y-4">
      <h3 className="text-base font-semibold mb-1">Choose a rollout strategy</h3>
      <p className="text-sm text-muted-foreground">
        Strict mode can be reverted at any time, but a careful rollout
        catches false positives before they affect every user.
      </p>

      <div className="space-y-2 mt-4">
        {options.map((o) => (
          <label
            key={o.value}
            className={cn(
              "flex items-start gap-3 p-3 rounded-md border cursor-pointer transition-colors",
              strategy === o.value
                ? "border-foreground bg-accent/50"
                : "hover:bg-accent/30"
            )}
          >
            <input
              type="radio"
              name="strategy"
              value={o.value}
              checked={strategy === o.value}
              onChange={() => setStrategy(o.value)}
              className="mt-1"
            />
            <div className="flex-1">
              <div className="text-sm font-medium">{o.label}</div>
              <div className="text-xs text-muted-foreground mt-0.5">
                {o.detail}
              </div>
            </div>
          </label>
        ))}
      </div>

      {strategy === "canary" && (
        <div className="p-3 rounded-md bg-muted/40">
          <label className="text-xs font-medium text-muted-foreground block mb-1">
            Canary application
          </label>
          <select
            value={canaryAppId}
            onChange={(e) => setCanaryAppId(e.target.value)}
            className="w-full rounded-md border bg-background px-3 py-1.5 text-sm"
          >
            {applications.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </div>
      )}

      {strategy === "phased" && (
        <div className="p-3 rounded-md bg-muted/40">
          <label className="text-xs font-medium text-muted-foreground block mb-1">
            Initial rollout: <span className="font-mono">{phasedPct}%</span>
          </label>
          <input
            type="range"
            min={1}
            max={100}
            value={phasedPct}
            onChange={(e) => setPhasedPct(parseInt(e.target.value, 10))}
            className="w-full"
          />
          <div className="text-xs text-muted-foreground mt-1">
            Will increase by 10% every 24h if FP rate stays under threshold.
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Step 3: Override path ───────────────────────────────────────────

function Step3Override({
  autoRevert,
  setAutoRevert,
  threshold,
  setThreshold
}: {
  autoRevert: boolean
  setAutoRevert: (b: boolean) => void
  threshold: number
  setThreshold: (n: number) => void
}) {
  return (
    <div className="space-y-4">
      <h3 className="text-base font-semibold mb-1">Safety net</h3>
      <p className="text-sm text-muted-foreground">
        Configure the auto-demote watchdog. If the live false-positive rate
        exceeds this threshold, the policy will automatically revert to
        Guideline mode after a short grace period.
      </p>

      <label className="flex items-start gap-3 p-3 rounded-md border cursor-pointer">
        <input
          type="checkbox"
          checked={autoRevert}
          onChange={(e) => setAutoRevert(e.target.checked)}
          className="mt-1"
        />
        <div className="flex-1">
          <div className="text-sm font-medium">
            Auto-revert to Guideline if FP rate spikes
          </div>
          <div className="text-xs text-muted-foreground">
            Recommended. Watchdog runs every minute on a 60-min rolling
            window.
          </div>
        </div>
      </label>

      {autoRevert && (
        <div className="p-3 rounded-md bg-muted/40 ml-6">
          <label className="text-xs font-medium text-muted-foreground block mb-1">
            Revert threshold:{" "}
            <span className="font-mono">{threshold}%</span> false positives
          </label>
          <input
            type="range"
            min={1}
            max={20}
            value={threshold}
            onChange={(e) => setThreshold(parseInt(e.target.value, 10))}
            className="w-full"
          />
          <div className="flex justify-between text-[10px] text-muted-foreground mt-1">
            <span>1% (very tight)</span>
            <span>20% (loose)</span>
          </div>
        </div>
      )}

      <div className="p-3 rounded-md bg-blue-50 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-900 text-xs">
        <div className="font-semibold text-blue-900 dark:text-blue-200 mb-1">
          Manual override is always available
        </div>
        <div className="text-blue-800 dark:text-blue-300">
          Reviewers (DPO, Security) can demote at any time with one click.
          End users see an &ldquo;appeal&rdquo; link in block messages.
        </div>
      </div>
    </div>
  )
}

// ─── Step 4: Approval ────────────────────────────────────────────────

function Step4Approval({
  approvers,
  strategy,
  instance
}: {
  approvers: string[]
  strategy: RolloutStrategy
  instance: PolicyInstance
}) {
  return (
    <div className="space-y-4">
      <h3 className="text-base font-semibold mb-1">Send for approval</h3>
      <p className="text-sm text-muted-foreground">
        These reviewers will receive a notification and can approve or
        reject the promotion. The policy stays in Guideline mode until all
        approvals land.
      </p>

      <div className="space-y-2">
        {approvers.map((role) => (
          <div
            key={role}
            className="flex items-center justify-between p-3 rounded-md border"
          >
            <div className="flex items-center gap-2">
              <span className="size-6 rounded-full bg-muted inline-flex items-center justify-center text-xs">
                ○
              </span>
              <span className="text-sm font-medium">{role}</span>
            </div>
            <Badge variant="outline">Pending</Badge>
          </div>
        ))}
      </div>

      <div className="rounded-md border p-3 text-xs space-y-1.5">
        <div className="text-muted-foreground">Summary</div>
        <div className="grid grid-cols-2 gap-2 mt-2">
          <span className="text-muted-foreground">Strategy:</span>
          <span className="capitalize font-medium">{strategy}</span>
          <span className="text-muted-foreground">Apps in scope:</span>
          <span className="font-medium">{instance.appliesTo.applicationIds.length}</span>
          <span className="text-muted-foreground">Severity:</span>
          <span className="capitalize font-medium">{instance.severity}</span>
        </div>
      </div>
    </div>
  )
}
