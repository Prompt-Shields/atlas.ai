# Runbook — onboarding a customer onto the Sentinel forwarder

Human checklist for taking one customer from "interested" to "events landing in
their workspace". Companion to [spec.md](../spec.md) §7 (the MVP slice this
implements) and [data-schema.md](../data-schema.md).

Roughly 30 minutes, most of it waiting on the customer's Azure admin. Two
people are needed: **their** Azure/Sentinel admin (steps 1–3) and **their**
Prompt Shields org admin (step 4). Steps 5–7 are ours.

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

## 7. Hand over

- [ ] Point their SOC at [kql-samples.md](../kql-samples.md).
- [ ] Set expectations on cost: ~150 events/day at ~500 bytes is ~22 MB/year for
      an 80-person deployment. Trivial, but it *is* on their Microsoft bill, not
      ours. The last query in kql-samples.md shows them the real number.
- [ ] Tell them what is not there yet: the workbook, analytic rules, the ASIM
      parser, and the Graph Security alerts channel are v1.1. Until then,
      high-severity events reach the analyst queue only via a scheduled
      analytics rule they create — the query is in kql-samples.md.
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
