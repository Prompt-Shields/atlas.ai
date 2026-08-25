# Policy Enforcement — Admin Guide

A walkthrough of how an admin actually uses the system, end-to-end.

## The mental model

Two ideas to internalize before anything else:

1. **Templates suggest, admins decide.** The product ships with 15 expert-authored templates (OWASP LLM Top 10, EU AI Act, GDPR, PCI/HIPAA, etc.). A template is read-only and prescriptive — it knows what *should* be done. But every policy you actually run is a **clone** of a template that *you own and tune* for your environment.

2. **Every policy has two lives: Guideline → Strict.** You don't go straight to enforcement. New policies start in **Guideline** mode (📘): they observe traffic, log what they *would* have caught, but alter nothing. Once you trust the data, you promote to **Strict** mode (🛡️): the same detectors now block, redact, or flag in real time. Strict can always be paused back to Guideline — that's the safety valve.

This separation is the whole point. It eliminates the hardest question in policy enforcement — *"is this rule going to break our customers?"* — by turning it into something you can answer with data before flipping the switch.

---

## The admin journey

### Day 1: clone a template

1. Navigate to **Policies** in the top nav (Shield icon).
2. Click **+ New from template** → land on the template library at `/dashboard/policy-enforcement/templates`.
3. Filter by category (e.g. "GDPR" or "OWASP LLM Top 10"), or search by OWASP reference (`LLM02`) or regulation (`Art. 9`).
4. Click any template card to read the full spec: rationale, example violation, detectors, actions, every tunable parameter with its default and help text.
5. Click **Clone & Customize**:
   - Give the policy a meaningful name (e.g. "PII Output — Customer Service" rather than the generic template name).
   - Optionally pre-select which applications it applies to.
   - Submit → you're redirected to your new policy's detail page.

The new policy is **always created in Guideline mode**, regardless of what the template suggests. This is non-negotiable — it's the system's promise that no admin can accidentally turn on enforcement without going through the explicit promotion flow.

### Day 1 → Day 14: tune and observe

On the policy detail page (`/dashboard/policy-enforcement/policies/<id>`), four tabs are immediately useful:

- **Detection** — see the detectors and tunable parameters. Each parameter has a level (`basic` / `advanced` / `expert`) so you can decide how deep to go. A locked 🔒 parameter means the template author marked it as not-safe-to-tune.
- **Scope** — review and edit *which applications* the policy applies to, plus broad scope filters (data classification, risk tier, department).
- **Test** — paste a sample prompt and click **Run test**. You see exactly which detectors fire, with confidence scores and matched substrings. This is a *pure simulation* — no state changes, no violations recorded. Use it freely to dial in parameter values before any traffic flows.
- **History** — track every mode change over the policy's life.

Meanwhile in the background, the policy is observing live traffic. Each near-miss is logged. After 30 days you have stats: total evaluations, would-block count, false-positive rate.

### Day 14+: promote to Strict

When your false-positive rate is acceptable (default threshold: <2%) and the policy has been observing for at least 14 days, the **Promote to Strict →** button at the top of the page becomes active. Until both gates are met, it shows *why* you can't promote yet — e.g. *"Need 8 more days in Guideline mode"* or *"FP rate is 3.4% — must be below 2%"*.

Click **Promote to Strict →**. A 4-step wizard opens:

1. **Risk preview** — blast radius math from the last 30 days of Guideline observation. *"Switching to Strict would have blocked 142 events / 0.31% of traffic, with 3 estimated false positives affecting 47 users."* No surprises.
2. **Rollout strategy** — pick `all` (immediate, full-coverage), `canary` (one application first), or `phased` (gradual % ramp).
3. **Override path** — set the auto-demote threshold (default: revert if live FP rate exceeds 5%). This is the watchdog.
4. **Approval** — see exactly which roles must sign off. The list is determined by the template's category:

   | Category | Required approvers |
   |---|---|
   | OWASP LLM | Security Lead |
   | EU AI Act | DPO + Security Lead |
   | GDPR | DPO |
   | Industry (PCI/HIPAA/Legal) | Security Lead + Compliance Officer |
   | Content Safety | Trust & Safety Lead |
   | Shadow AI | Security Lead |

   Click **Send for approval**. The policy stays in Guideline mode. A pending-approval banner now appears on the detail page.

### The approval handshake

The policy doesn't move until *every* required role signs off. On the **Approvals** tab (or the prominent banner card on the detail page), each approver clicks **Approve as <role>** or **Reject**.

- One rejection freezes the request. The policy stays in Guideline. Submit a new promotion request when ready.
- When the **last** approval lands, the system **automatically flips** the policy to Strict mode and starts enforcing. A toast confirms: *"✓ Promoted to Strict — all approvals collected"*.

### Steady state: enforcing

Once Strict, the detail page changes shape:

- The mode toggle now displays a green **● LIVE** badge and live stats: *blocks/30d*, *FP rate*, *rollout strategy*.
- A **WatchdogBanner** appears at the top whenever needed. It polls the watchdog endpoint every 15 seconds, comparing the live FP rate to your auto-demote threshold:
  - **Happy path** — banner doesn't show. You're invisible to the system.
  - **Grace period started** — *"FP rate 5.2% > 5% threshold — auto-demote at T-04:32"*. A live countdown ticks every second.
  - **Recovered** — *"Recovered — back below threshold"*. Brief banner, then disappears.
  - **Auto-demoted** — *"🚨 Auto-demoted by watchdog: FP rate sustained above threshold for 300s"*. The policy is now back in Guideline. The history tab records the watchdog as the actor.

### Manual override at any time

Admins can demote a Strict policy to Guideline at any point — **no approvals required**. This is the design's safety valve: increasing safety (less enforcement) never needs second-signature. Click **⏸ Pause to Guideline** on the mode toggle, confirm, and you're back in observation mode.

---

## State at a glance

```
                    NEW INSTANCE
                         │
                         ▼
                  ┌─────────────┐
                  │  GUIDELINE  │◄──────────┐
                  │     📘      │           │
                  │  log only   │           │
                  └─────────────┘           │
                         │                  │
                  Promote request           │
                         │                  │
                         ▼                  │
              [Pending approvals]           │
                         │                  │
              All approvers approve         │
                         │                  │
                         ▼                  │
                  ┌─────────────┐           │
                  │   STRICT    │           │
                  │     🛡️      │           │
                  │ block/redact│           │
                  └─────────────┘           │
                         │                  │
                  ┌──────┴──────┐           │
                  │             │           │
              Manual         Watchdog       │
              "Pause"        auto-demote    │
                  │             │           │
                  └──────┬──────┘           │
                         │                  │
                         └──────────────────┘
                          (always allowed)
```

---

## Eligibility gates explained

When a Guideline policy isn't yet promotable, the **PromotionChecklist** below the mode toggle shows three checks with progress bars:

| Check | Default | What it means |
|---|---|---|
| Days in Guideline | 14 days | The policy has been observing live traffic long enough to produce statistically meaningful FP data. |
| False-positive rate | <2% | Of all "would-block" events, fewer than 2% were marked as legitimate-but-flagged. |
| Approvals | varies | Required roles have signed off on this specific promotion request. |

All three must pass before **Promote to Strict →** is clickable. Until then the button is disabled and the checklist explains exactly which gate is blocking you.

The thresholds live in `backend/app/services/policy_promotion.py` as `PROMOTION_DEFAULTS` and can be tuned per environment without changing the UI.

---

## Roles and responsibilities

| Role | What they can do |
|---|---|
| **Policy author** (anyone) | Clone templates, edit Guideline-mode parameters, submit promotion requests, run Test Console, demote any Strict policy to Guideline. |
| **DPO** | Approve promotion of GDPR + EU AI Act policies. Override DPO-gated blocks (e.g. Art. 9 special category). |
| **Security Lead** | Approve promotion of OWASP, EU AI Act, Industry, Shadow AI policies. |
| **Compliance Officer** | Approve promotion of Industry (PCI/HIPAA/Legal) policies alongside Security Lead. |
| **Trust & Safety Lead** | Approve promotion of Content Safety policies. |
| **Watchdog** (system) | Auto-demote any Strict policy whose FP rate breaches its configured threshold for the configured grace period. Acts as `by: "watchdog"` in the promotion history. |

> **Demo note:** in this build, any visitor can click "Approve as <role>" — there's no auth wired up. In production this is gated by SSO + role mapping. The `PendingApprovalsCard` has a comment to that effect so reviewers don't mistake demo for prod.

---

## What admins **don't** have to think about

The system deliberately removes whole categories of decision so admins can focus on policy quality:

- **Storage of raw prompts** — never. Only `promptHash` (SHA-256) and detector evidence are persisted. The PEP ingest endpoint hard-rejects anything that doesn't carry exactly a 64-char hex digest.
- **What happens if an approver disappears** — the system explicitly tracks who's pending, and another admin in the same role can sign off.
- **What if a template author publishes v1.1 of LLM02** — instances pin to the version they cloned from (`templateVersion` field). An upgrade banner appears; the admin can re-clone to adopt new defaults but existing tuning is preserved.
- **What if the watchdog is too aggressive** — admins set the FP threshold per policy at promotion time, and grace period is configurable. False alarms manifest as `recovered` events in the watchdog logs, not unplanned demotions.

---

## Common scenarios

**"My team wants to enforce a brand-new policy by tomorrow."**
You can't, by design. Minimum 14 days observation + clean FP rate + approvals. The 14 days is configurable in `backend/app/services/policy_promotion.py` (`PROMOTION_DEFAULTS.days_in_guideline_required`) but lowering it below ~7 days isn't recommended unless you have very high traffic.

**"We need a policy that just logs forever, never blocks."**
Some templates are designed for that — e.g. *EU AI Act Art. 15 Audit Log* has its `immutable_store` parameter locked and ships with `enforcementMode: "log"` as the *target*. Promoting it doesn't change behaviour, it just formalises the live commitment.

**"A Strict policy just auto-demoted in the middle of the night."**
Open the policy detail page → History tab. You'll see a `strict → guideline` event with `by: watchdog` and the reason ("FP rate 7.3% sustained above 5% for 300s"). Investigate the violations feed, retune parameters in Guideline mode, then re-submit for promotion. Auto-demote is a feature, not a failure.

**"Someone approved a promotion they shouldn't have."**
Demote it. Manual demote is always allowed and never needs approval. Then file an issue against the approver's role — the history tab preserves the approver's ID and timestamp.

**"I want to test a policy change against last week's traffic before promoting."**
Today: use the Test Console to paste representative prompts. Future enhancement: replay-mode that re-evaluates the last N days of recorded `promptHash`-keyed events with new parameter values.

---

## Reference: API surface

For automation and external integrations, the same actions are available via REST:

| Route | Method | Purpose |
|---|---|---|
| `/api/v1/policies` | GET | List active instances (PEP-facing bundle) |
| `/api/v1/policies/violations` | POST | On-device PEPs ingest violations here |
| `/api/v1/policies/violations` | GET | Read recent violations for the audit-log UI |
| `/api/v1/policies/clone` | POST | Server-side clone of template → new Guideline instance |
| `/api/v1/policies/{id}` | GET | Fetch instance + template |
| `/api/v1/policies/{id}/promote` | POST | Initiate promotion request |
| `/api/v1/policies/{id}/approve` | POST | Per-role sign-off; auto-flips to Strict on full approval |
| `/api/v1/policies/{id}/demote` | POST | Strict → Guideline (always allowed) |
| `/api/v1/policies/{id}/evaluate` | POST | Test Console dry-run |
| `/api/v1/policies/watchdog/tick` | POST | Run auto-demote watchdog (cron target) |
| `/api/v1/policies/watchdog/tick` | GET | Read-only watchdog snapshot |

All endpoints are JWT-authenticated (Bearer token). Tenant scoping is derived from the JWT's `tenant_id` claim and enforced both at the application layer (explicit `WHERE tenant_id = ...`) and at the database layer (RLS via `SET LOCAL app.current_tenant_id`). See [the merge spec](./superpowers/specs/2026-05-05-ai-spm-merge-design.md) for the full auth + tenancy model.

The Test Console and watchdog endpoints both have request-side guards (10KB prompt cap; rate-limited demote action) so external clients can't abuse them.
