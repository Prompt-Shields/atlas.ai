# Runbook — onboarding a customer onto the Sentinel forwarder

Human checklist for taking one customer from "interested" to "events landing in
their workspace". Companion to [spec.md](../spec.md) §7 (the MVP slice this
implements) and [data-schema.md](../data-schema.md).

Roughly 30 minutes, most of it waiting on the customer's Azure admin. Two
people are needed: **their** Azure/Sentinel admin (steps 1–3) and **their**
Prompt Shields org admin (step 4). Steps 5–9 are ours.

---

## Before you start

- [ ] The customer has a Sentinel-enabled Log Analytics workspace, and you know
      its name and region.
- [ ] Their Azure admin can create app registrations and assign roles — the
      role assignment in step 3 needs Owner or User Access Administrator on the
      resource group.
- [ ] They have accepted the prompt-content stance: **we never ship the prompt
      body**, only structured fields plus a SHA-256 hash. This is irreversible
      in the sense that it shapes what their SOC can ever see; confirm it now
      rather than after the first incident (spec §8, open question 3).

---

## 1. Customer creates an app registration

In *their* Azure AD tenant. This is the identity Prompt Shields authenticates
as when writing to their workspace.

```bash
az ad app create --display-name "Prompt Shields Sentinel Forwarder"
# note the appId  → this is the "client id"

az ad sp create --id <appId>
az ad sp show --id <appId> --query id -o tsv
# note the object id → this is `forwarderPrincipalId` for step 2

az ad app credential reset --id <appId> --years 1
# note the password → this is the "client secret"
```

Record for step 4: **tenant id**, **client id**, **client secret**, and keep the
**object id** for step 2.

> The secret is the one piece of this that expires. Put its expiry in the
> customer's calendar now — a lapsed secret shows up as `auth_failed` dead
> letters, not as an outage anyone notices.

## 2. Customer runs the Bicep template

From [`infra/sentinel-customer-setup.bicep`](../../../../infra/sentinel-customer-setup.bicep),
in Azure Cloud Shell, against the resource group holding the workspace:

```bash
az deployment group create \
  --resource-group <rg-with-the-sentinel-workspace> \
  --template-file sentinel-customer-setup.bicep \
  --parameters workspaceName=<workspace> \
               forwarderPrincipalId=<object-id-from-step-1>
```

This creates the `PromptShieldsActivity_CL` table, a Data Collection Endpoint,
a Data Collection Rule declaring the `Custom-PromptShields_v1` stream, and a
**Monitoring Metrics Publisher** role assignment scoped to that DCR alone — not
the subscription. If their security team asks: that scope is the whole point,
so a leaked credential can write one stream and nothing else.

Record the deployment outputs: **dceUrl**, **dcrImmutableId**, **streamNameOut**,
**tableNameOut**.

## 3. Confirm the role assignment landed

Role assignments propagate asynchronously; a forward attempted immediately
after deployment can still 403.

```bash
az role assignment list --assignee <object-id> --all -o table
```

Wait for `Monitoring Metrics Publisher` to appear, then give it another minute.

## 4. Org admin connects Sentinel in Prompt Shields

Dashboard → **Integrations → Microsoft Sentinel → Connect**:

- **Workspace label** — free text, e.g. "Acme Corp SOC".
- **Azure tenant id / client id / client secret** — from step 1.
- **Data Collection Endpoint URI / DCR immutable id / stream name / table
  name** — from step 2.
- **Data mapping** — which of the five event types stream into the table.
  Default is all five. Dropping `Coached` is the usual first economy if they
  are cost-sensitive.

The secret is stored Fernet-encrypted and never returned by the API.

Submitting only the workspace label (no Azure fields) is valid and leaves the
integration in **preview** mode: the dashboard shows a seeded event stream and
nothing is sent to Azure. Useful for a demo before their admin has run step 2.

## 5. Prove it end to end

Hit **Forward now** (`POST /api/v1/integrations/sentinel/forward`), then in
their workspace:

```kql
PromptShieldsActivity_CL
| where TimeGenerated > ago(24h)
| summarize count() by EventType, Severity
```

First ingestion into a brand-new custom table can take **10–15 minutes** to
become queryable. Do not start debugging before then — a `batches_sent` of 1
with no dead letters means Azure Monitor accepted the batch.

More queries to hand them: [kql-samples.md](../kql-samples.md).

### If nothing arrives

Check `GET /api/v1/integrations/sentinel/status` first. `pending_dead_letters`
tells you which of these it is:

| Symptom | Cause | Fix |
|---|---|---|
| `events_read` is 0 | No policy-relevant telemetry in the window. Ordinary allowed prompts are never forwarded. | Have someone paste something that trips a policy, wait a minute (see `COMMIT_LAG`), forward again. |
| `events_read` > 0, `events_forwarded` 0, no dead letters | Every event was filtered out by the data mapping, or lacked a `prompt_hash`. | Check the enabled event types in the wizard. |
| Dead letters, reason `http_403` | Role assignment missing or not yet propagated. | Redo step 3, then replay (step 6). |
| Dead letters, reason `auth_failed` or `http_401` | Wrong or expired client secret. | Rotate in step 1, reconnect in step 4, replay. |
| Dead letters, reason `schema_invalid` | Our mapper and their DCR disagree on columns. | Escalate to engineering — do not edit the customer's DCR by hand. |
| Dead letters, reason `exhausted_retries` | Azure outage or network. | Replay once Azure recovers. |
| `batches_sent` > 0 but the table is empty after 20 minutes | DCR `dataFlows` misrouted, or the table was created after the DCR. | Re-run step 2; the template orders these correctly. |

## 6. Replay anything that was dead-lettered during setup

A 403 during the propagation window is normal. Nothing is lost — it is in the
dead-letter queue with its full payload.

```bash
python -m scripts.replay_sentinel_dead_letters list   --tenant-slug <slug>
python -m scripts.replay_sentinel_dead_letters replay --tenant-slug <slug> --dry-run
python -m scripts.replay_sentinel_dead_letters replay --tenant-slug <slug>
```

Replayed payloads carry their original `EventId` values, so a batch that was
partly ingested before failing does not duplicate.

## 7. Deploy the analytics rules

Only once step 5 shows events arriving — rules over an empty table are harmless
but prove nothing, and you want to be able to tell "the rule works" from "there
was nothing to alert on".

From the `infra/` directory (the template loads its rule definitions from a
sibling JSON file, so the relative path must resolve):

```bash
az deployment group create \
  --resource-group <rg-with-the-sentinel-workspace> \
  --template-file sentinel-analytic-rules.bicep \
  --parameters workspaceName=<workspace>
```

This creates four scheduled rules that raise **incidents** into their analyst
queue:

| Rule | Fires when | Severity |
|---|---|---|
| High-severity AI activity | any event at `Severity == "High"` | High |
| Repeated blocked prompts of the same sensitive type | one user blocked ≥3× in 1 h on the same `SensitiveType` | High |
| New shadow AI tool in use | an unsanctioned tool not seen in the prior 14 days | Medium |
| Bias-flagged prompt | `EventType == "BiasFlagged"` | Medium |

Nervous customer, or a busy production workspace? Deploy with
`--parameters rulesEnabled=false` first, review the rules in the portal, then
redeploy without the flag. Rule names are derived from stable ids, so a
redeploy updates in place rather than creating duplicates.

**Thresholds** (≥3 in 1 h; the 14-day shadow-AI baseline) are the spec's
proposed defaults and are **pending security-PM sign-off**. They are `let`
statements at the top of each query, so the customer can tune them in the
portal without rewriting the logic — tell them that explicitly, because the
first week is when they will want to.

**Bias-flagged is not a SOC alert in most orgs.** It asserts no MITRE tactic and
usually belongs with whoever owns responsible-AI policy. Ask where they want it
routed rather than assuming the SOC queue.

## 8. Deploy the workbook

Gives them the in-Sentinel dashboard over the same table. Also from `infra/`:

```bash
az deployment group create \
  --resource-group <rg-with-the-sentinel-workspace> \
  --template-file sentinel-workbook.bicep \
  --parameters workspaceName=<workspace>
```

Then: Sentinel → **Workbooks** → *Prompt Shields — AI activity*. It has a time
range and an AI-tool filter at the top, then at-a-glance counts, activity over
time, sanctioned-vs-shadow tool usage, what sensitive data is being caught,
which departments and people, and a `PromptHash` correlation view.

Like the rules, deploying against an empty table is harmless — every tile just
renders empty until events arrive.

## 9. Deploy the ASIM parser

Only if they use ASIM — ask. Large enterprises with an existing Sentinel practice will;
a foundation-segment customer will not know what it is, and installing it costs them
nothing but explains nothing either.

```bash
az deployment group create \
  --resource-group <rg-with-the-sentinel-workspace> \
  --template-file sentinel-asim-parser.bicep \
  --parameters workspaceName=<workspace>
```

Verify in their workspace — this should return rows once events have arrived:

```kql
vimAuditEventPromptShields
| where TimeGenerated > ago(24h)
| summarize count() by Operation, EventResult
```

**What it buys them.** Not a nicer way to query our table; they already had that. It is
that an ASIM query which never mentions Prompt Shields — "every audit event for this
actor" — starts returning AI policy decisions next to their Exchange and Azure Activity
events. Without it our table is a silo their existing detections cannot see.

Two answers worth having ready, because a competent ASIM user will ask:

- **"Why is `EventType` always `Other`?"** ASIM's `EventType` is a closed set describing
  operations on an object. Our events are policy decisions about an AI interaction and
  fit none of them, so the specific decision lives in `Operation` — that is the field to
  filter on, e.g. `Operation == "Blocked"`.
- **"Why does `EventResult` say `Failure` for a blocked prompt when the control worked?"**
  It reflects the user's outcome, not the control's. `EventResult == "Failure"` means the
  prompt did not go through, which is what an analyst hunting denied actions wants.

To add it to a custom unifying parser, it takes the standard AuditEvent filtering
signature, so it drops into a `union` alongside the built-ins without adaptation. Note
that `srcipaddr_has_any_prefix` and `newvalue_has_any` return nothing rather than being
ignored — we record neither field, so a query filtering on them is asking for rows this
source cannot produce.

## 10. Hand over

- [ ] Point their SOC at [kql-samples.md](../kql-samples.md).
- [ ] Set expectations on cost: ~150 events/day at ~500 bytes is ~22 MB/year for
      an 80-person deployment. Trivial, but it *is* on their Microsoft bill, not
      ours. The last query in kql-samples.md shows them the real number.
- [ ] Confirm the four rules from step 7 show as **Enabled** in
      Sentinel → Analytics, and that they know how to tune the thresholds.
- [ ] Confirm the workbook from step 8 opens and renders. If every tile is
      empty but `/status` shows events forwarded, the table name in the workbook
      and the DCR disagree — escalate rather than editing the workbook by hand.
- [ ] If you skipped step 9, tell them the **ASIM parser** exists and what it is for —
      their existing ASIM-based queries will not pick this table up until it is
      installed. Large enterprises are the ones who ask.
- [ ] If they ask about a real-time alert channel: alerting runs on the rule
      schedule (hourly for high severity), not at send time. That is a
      deliberate design change — third-party alert creation through the Graph
      Security API is not a supported path. NRT rules are the escalation if they
      genuinely need sub-minute.
- [ ] Diarise the client-secret expiry from step 1.

---

## Operating notes

**Where the forwarder runs.** In our control plane, as the `sentinel_forwarder`
worker mode (`WORKER_MODE=sentinel_forwarder`), sweeping every tenant each
cycle. One customer's bad config never stops another's delivery.

**What is guaranteed.** Every event either lands in Sentinel or is visible in
the dead-letter queue. The cursor advances only past batches Azure Monitor
confirmed accepting, so a crash mid-batch re-sends rather than skips —
duplicates are recoverable, gaps in an audit trail are not.

**Backfill.** A freshly connected integration starts one day back, not from the
beginning of time. Connecting Sentinel should not replay a year of telemetry
into a workspace the customer pays for by the gigabyte.

**Client secret vs. federated credential.** v1 stores an encrypted client
secret. The spec prefers a Federated Identity Credential — no secret to rotate
or leak — which needs a deployed Prompt Shields app registration for the
customer to federate against. That is the v1.1 upgrade; the connect wizard
takes the same coordinates either way, so the migration is credential-only.
