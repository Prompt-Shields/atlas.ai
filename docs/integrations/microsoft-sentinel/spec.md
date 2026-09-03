# Microsoft Sentinel integration — scoping & architecture

> **Status.** The §7 MVP slice has shipped: the setup wizard takes the Azure
> Monitor coordinates, the forwarder streams prompt telemetry through the Logs
> Ingestion API with batching, retry, and a dead-letter queue, the Bicep
> template is at [`infra/sentinel-customer-setup.bicep`](../../../infra/sentinel-customer-setup.bicep),
> and the replay CLI is `backend/scripts/replay_sentinel_dead_letters.py`.
> Onboarding checklist: [runbooks/customer-onboarding.md](runbooks/customer-onboarding.md).
>
> **v1.1 analyst queue has shipped too**, as scheduled analytics rules —
> [`infra/sentinel-analytic-rules.bicep`](../../../infra/sentinel-analytic-rules.bicep)
> over [`sentinel-analytic-rules.json`](../../../infra/sentinel-analytic-rules.json).
> This **supersedes the Graph Security alerts channel** proposed in §2; see the
> correction there for why that path is not available.
>
> **The v1.1 workbook has shipped** —
> [`infra/sentinel-workbook.bicep`](../../../infra/sentinel-workbook.bicep) over
> [`sentinel-workbook.json`](../../../infra/sentinel-workbook.json).
>
> The ASIM parser has since shipped too —
> [`sentinel-asim-parser.json`](../../../infra/sentinel-asim-parser.json), and so
> has the data connector tile, though **not** via the Codeless Connector
> Platform — see the §5 correction. Nothing in the spec remains unbuilt.

> **Naming.** "Sentinel" in this document means **Microsoft Sentinel** (Microsoft's SIEM, unified into the Defender portal — see [Microsoft Sentinel overview](https://learn.microsoft.com/en-us/azure/sentinel/overview?tabs=defender-portal)). The dashboard's stack badges currently mention **SentinelOne** (an unrelated EDR vendor); many customers run both. This document covers Microsoft Sentinel only.

## 1. What "export reports to Sentinel" can mean

Three plausible interpretations, in increasing scope:

**A. Periodic report artifact** — bulk push of the rendered audit report (the same one `/audit-report` produces) into Sentinel as a single record per export. Lowest engineering cost; least useful operationally.

**B. Continuous event stream** — every Prompt Shields event (Redacted / Anonymised / Blocked / Coached / Bias-flagged) lands in Sentinel as a log row in near-real-time. Customer SOCs can pivot from Sentinel investigations into AI activity, correlate with Defender for Cloud Apps / Entra sign-ins / Purview DLP. This is what most security teams actually want.

**C. Full Sentinel solution package** — stream **plus** a published Sentinel solution including a custom data connector, ASIM parser, workbook, analytic rules, watchlists, and hunting queries. Customers install it from Content Hub with one click.

**Recommendation**: scope C, sequenced — ship B in v1 (event stream is the load-bearing piece), then layer the workbook / rules / parser as v1.1. A in isolation isn't worth the integration complexity. The current `/audit-report` PDF stays as the human-readable export; Sentinel becomes the machine-readable one.

## 2. Sentinel ingestion paths — which to use

Five options Microsoft documents; only two are appropriate here.

| Path | Status | Use? |
|---|---|---|
| **Logs Ingestion API** (Azure Monitor) → custom DCR → custom table | Current recommended | **Yes — primary** |
| Log Analytics HTTP Data Collector API | Deprecated, sunset | No |
| Codeless Connector Platform (CCP) | GA, declarative JSON | **No — it polls; we push. See the §5 correction.** The UX goal is met by a `Static` data connector instead |
| Common Event Format / Syslog forwarder | Network gear, on-prem | No |
| Microsoft Graph Security API (alerts) | GA for *reading*; third-party writes deprecated | **No — see the correction below** |
| Sentinel **scheduled analytics rules** over the custom table | GA | **Yes — this is the alerts path** |

**Two-channel design** (as originally proposed):

- **Telemetry** (every event) → Logs Ingestion API → custom table `PromptShieldsActivity_CL`
- **Actionable alerts** (high-severity events / repeated bias-flagged / repeated blocked) → Graph Security API → Sentinel incidents queue

Why two channels: alerts in Sentinel are first-class (analyst queue, automation, SLA tracking). Burying high-sev events as plain log rows means the SOC has to write a detection rule before they see anything. The separate alert path gives them an inbox out of the box.

> ### ⚠️ Correction (2026-08-28): the alerts channel is not buildable as specified
>
> The table above marks "Microsoft Graph Security API (alerts)" as GA and
> usable. That is right for *reading* alerts and wrong for *writing* them, which
> is what this design needs. Third-party alert **creation** is not a supported
> operation: the legacy `/security/alerts` API is deprecated with a retirement
> date announced, and its replacement (`alerts_v2`, `microsoft.graph.security`)
> is read-oriented — a partner consumes alerts through it, it does not accept
> partner-authored ones.
>
> **The supported mechanism is a scheduled analytics rule** over the custom
> table. Sentinel runs the rule's KQL on a schedule and raises a first-class
> **incident** from the results, which is exactly the outcome the second channel
> was for — the analyst queue, automation, and SLA tracking all apply to
> incidents raised this way. Rules also ship as ARM/Bicep, so they deploy with
> the rest of the customer's setup instead of needing a running service.
>
> So the goal stands and the mechanism changes. The revised design is
> **one delivery channel, two consumption paths**:
>
> - **Telemetry** (every event) → Logs Ingestion API → `PromptShieldsActivity_CL`
> - **Actionable alerts** → *scheduled analytics rules over that table* →
>   Sentinel incidents queue
>
> This is strictly less machinery than the original: no second outbound
> integration, no second set of per-tenant credentials, no alert-promotion state
> to keep consistent with the stream, and nothing to replay when the alert path
> fails independently of the log path. The cost is latency — a rule fires on its
> schedule (hourly for the high-severity rule) rather than at send time. For a
> queue a human works, that is an acceptable trade; if sub-minute alerting is
> ever required, NRT (near-real-time) rules are the escalation, not a return to
> the Graph API.
>
> Shipped in [`infra/sentinel-analytic-rules.bicep`](../../../infra/sentinel-analytic-rules.bicep);
> rule definitions and their thresholds live in
> [`sentinel-analytic-rules.json`](../../../infra/sentinel-analytic-rules.json).
> Item 5 of the forwarder's responsibilities in §3 ("alert promotion rule") is
> therefore **not implemented in the forwarder** and should not be — it belongs
> in the rules.

## 3. Architecture (target state)

```
Prompt Shields Desktop Agent  ──┐
                                │  redaction / coach / block events
Prompt Shields Backend  ────────┤
   (multi-tenant control plane  │
    that today produces the     │
    /ai-activity-log content)   │
                                ▼
                    ┌───────────────────────────┐
                    │  Sentinel Forwarder       │  (new component)
                    │  - Per-tenant config      │
                    │  - Batching + retry       │
                    │  - Schema mapping         │
                    │  - Dead-letter queue      │
                    └───────────────┬───────────┘
                                    │  all events
                                    ▼
                     ┌──────────────────────────┐
                     │ Azure Monitor Logs       │
                     │ Ingestion API            │
                     │  + Data Collection Rule  │
                     │  + Data Collection Endpt │
                     └────────────┬─────────────┘
                                  │
                                  ▼
              ┌──────────────────────────────────────┐
              │  Microsoft Sentinel                  │
              │  (Defender portal)                   │
              │  ┌────────────────────────────────┐  │
              │  │ PromptShieldsActivity_CL       │  │ ← log table
              │  │            │                   │  │
              │  │            ▼                   │  │
              │  │ Scheduled analytic rules  ✔    │  │ ← the alerts path
              │  │            │                   │  │
              │  │            ▼                   │  │
              │  │ Incidents queue                │  │ ← what the SOC works
              │  │                                │  │
              │  │ Workbook                  ✔    │  │
              │  │ ASIM Parser               ✔    │  │
              │  └────────────────────────────────┘  │
              └──────────────────────────────────────┘
```

> The alert path runs **inside** Sentinel rather than as a second outbound
> channel from the forwarder — see the §2 correction. The forwarder delivers one
> stream; the rules decide what becomes an incident.

The **Sentinel Forwarder** is the new component. Everything upstream already exists. Forwarder responsibilities:

1. **Per-tenant config**: each customer registers an Azure AD application in *their* tenant, gives it `Monitoring Metrics Publisher` (or narrower DCR-scoped role), and pastes `tenantId / clientId / clientSecret-or-FIC / DCE URL / DCR Immutable ID / Stream Name / Table Name` into Prompt Shields admin settings.
2. **Schema mapping**: Prompt Shields' internal event → flat Sentinel column shape (see [data-schema.md](data-schema.md)).
3. **Batching + backoff**: Logs Ingestion API accepts up to 1 MB / 32 k items per request. Batch by 5 s window or 500 events, whichever first. Exponential backoff on 429 / 503.
4. **Dead-letter queue**: failed batches go to durable storage with the full payload + error so we can replay without losing audit data.
5. ~~**Alert promotion rule**: any event with `Severity = High`, plus configured triggers (e.g. ≥3 blocked Compensation prompts in 1 h from same user), is *additionally* emitted via Graph Security alerts.~~ **Superseded** — see the §2 correction. Promotion happens in Sentinel, as scheduled analytics rules over the table; the forwarder has one job, which is delivering the stream.

## 4. Data schema

The custom log table column-level schema lives in [data-schema.md](data-schema.md). Headline rules:

- `TimeGenerated` is the only column Sentinel requires; equals event timestamp.
- We **never ship the original prompt body**. The product's whole premise is that prompts contain PII; relaying that PII to Sentinel re-creates the leak. We send a `Detail` description plus `PromptHash` (SHA-256) so the customer can correlate without storing content.
- `UserAadObjectId` is denormalized so KQL joins with `SigninLogs` work without a lookup table.
- The ASIM parser maps this onto `imAuditEvent` so customers' existing ASIM-based queries reach our events without changes. Shipped as the source-specific filtering parser `vimAuditEventPromptShields` — see §7. (This line previously said v1.1 while §8 q6 said v2; the two disagreed until it shipped.)

## 5. Authentication & tenancy

- **Per-customer Azure AD app registration** in *their* tenant. They give it the minimum scope: `Monitoring Metrics Publisher` on the specific DCR (not tenant-wide). They create DCR + DCE in their workspace via the Bicep template at [infra/sentinel-customer-setup.bicep](../../../infra/sentinel-customer-setup.bicep).
- Prompt Shields stores `clientId` and either an encrypted `clientSecret` **or — preferred — a Federated Identity Credential (FIC)** against Prompt Shields' Azure AD app. FIC eliminates secret rotation and the blast radius of a leaked secret.
- **Multi-tenant Prompt Shields side**: the forwarder reads tenant config from a per-customer settings table, fetches an Azure AD token for the customer tenant via FIC, signs the Logs Ingestion request.

**Decision point** (deferred to v1.1): also offer the Sentinel **Codeless Connector Platform** path, so the customer's Sentinel admin can install Prompt Shields from Content Hub and have the connection wired automatically. Cleaner UX, but requires us to publish or have the customer import a custom connector.

**Correction — CCP is the wrong mechanism, and the goal is met another way.** The
Codeless Connector Framework has one connector kind, `RestApiPoller`: it exists so
Sentinel can *periodically fetch* from a vendor's API. Prompt Shields pushes through the
Logs Ingestion API. Adopting CCP would mean inverting the delivery model — standing up a
customer-authenticated polling API, re-solving batching, retry and backpressure on
Microsoft's schedule instead of ours, and running two ingestion paths at once — to
deliver a tile.

What the decision point actually wanted was the *UX*: a Prompt Shields entry in the Data
connectors gallery with setup instructions and a live connected indicator. That is a
**`Static` data connector definition**
([`infra/sentinel-data-connector.json`](../../../infra/sentinel-data-connector.json)),
which is what Microsoft's own reference prescribes: `kind: Customizable` is for API
polling connectors, `Static` for everything else, with `connectivityCriteria` of type
`isConnectedQuery` — a KQL query over the custom table — rather than `HasDataConnectors`,
which reports on poller connections we deliberately do not have.

The tile carries the three setup steps, the ingestion graph, sample queries (including
one through the ASIM parser), and the prompt-content stance, so a security reviewer
deciding whether to let a third party write into their SIEM reads it on the tile rather
than only in a runbook. Without a tile, "is Prompt Shields actually delivering?" is a
question the SOC can answer only by knowing our table name and writing a query.

## 6. Failure modes & guarantees

| Failure | Behavior |
|---|---|
| Network blip mid-batch | Retry with exponential backoff, jittered (5 attempts) |
| 401 (token expired) | Refresh token, retry once |
| 403 (DCR permissions) | Move batch to dead-letter, emit operational alert to *Prompt Shields* admin (not customer SOC — they can't fix it) |
| 413 (payload too big) | Halve batch, retry |
| 429 (rate limit) | Honor `Retry-After`, drop to slow-lane queue |
| Sustained outage | Dead-letter queue with replay tool; durability target = last 7 days minimum |
| Schema drift (we add a column) | Sentinel rejects unknown columns by default → DCR must be updated. Forwarder pre-validates against latest DCR schema and rejects-at-source rather than poisoning the customer's table |

**Audit guarantee**: every event must land in Sentinel exactly once or be visibly in the dead-letter queue. No silent loss. Idempotency by `EventId`: send-only-after-confirmed-accept (single-flight per event) is simpler than dedup-on-receive, and Sentinel's Logs Ingestion API doesn't dedupe natively.

## 7. MVP slice

To minimize scope while staying demoable:

1. **Customer setup wizard** in Prompt Shields admin — one form capturing `Tenant ID`, `DCE URL`, `DCR Immutable ID`, `Stream Name`, `Table Name`, plus an Azure AD app registration step that walks the customer through `Monitoring Metrics Publisher` on the DCR.
2. **Forwarder service** with batching, retry, and dead-letter — **stream channel only** (no Graph Security API alerts yet).
3. **Bicep template** ([infra/sentinel-customer-setup.bicep](../../../infra/sentinel-customer-setup.bicep)) the customer runs in Azure Cloud Shell; creates DCE, DCR, custom table, role assignment.
4. **Sample KQL** in this folder that proves it works:
   ```kql
   PromptShieldsActivity_CL
   | where TimeGenerated > ago(24h)
   | summarize count() by EventType, Severity
   ```
5. **Replay CLI** for the dead-letter queue.

Workbook, analytic rules, parser, alerts channel, and codeless connector all land in v1.1.

**Update:** analytic rules have shipped (they *are* the alerts channel — see the §2 correction), and so have the workbook, the ASIM parser, and the data connector tile — the last as a `Static` connector rather than CCP, per the §5 correction. **Nothing in this section remains unbuilt.**

### ASIM parser (shipped)

`vimAuditEventPromptShields`, a source-specific ASIM **AuditEvent** filtering parser
(schema version 0.1.2), deployed as a workspace `savedSearch` in the `ASIM` category by
[`infra/sentinel-asim-parser.bicep`](../../../infra/sentinel-asim-parser.bicep).

The point is not that a customer can query *us* more conveniently — they already could.
It is that an ASIM query which never mentions Prompt Shields, "every audit event for this
actor", starts returning AI policy decisions alongside their Exchange and Azure Activity
events. Without a parser our table is a silo their existing detections cannot see, which
is exactly what large enterprises object to.

Two mapping decisions are worth knowing, because both are judgement calls:

- **`EventType` is `Other` for every event.** ASIM's `EventType` is a closed set
  (Set / Read / Create / Delete / Execute / …) describing operations on an object. Our
  events are policy decisions about an AI interaction and belong to none of them. Forcing
  `Blocked` into `Delete`, or `Coached` into `Set`, would file our rows under a meaning
  they do not have. The specific decision lives in `Operation`, which is the mandatory
  free-text field intended for it — so `Operation == "Blocked"` is how an analyst finds
  blocked prompts.
- **`EventResult` reflects the *user's* outcome, not the control's.** Blocked maps to
  `Failure` (the prompt did not go through), Redacted and Anonymised to `Partial`, Coached
  and BiasFlagged to `Success`. Mapping everything to `Success` on the grounds that
  enforcement worked would be defensible and useless: an analyst filtering
  `EventResult == "Failure"` wants denied actions.

Two filter parameters this source cannot honour, `srcipaddr_has_any_prefix` and
`newvalue_has_any`, return **no rows** rather than being ignored. We record neither a
source IP nor a changed value, so a caller filtering on them is asking for rows we cannot
produce; silently dropping the filter would hand back unrelated rows as though they
matched.

## 8. Open questions / decisions

1. **Scope confirmation**: B alone, or B + v1.1 packaging (workbook + rules)? *Default: B for v1, queue C for v1.1.*
2. **Where the forwarder runs**: in the Prompt Shields control plane (multi-tenant SaaS), or a per-customer relay inside their tenant? *Default: SaaS unless a customer asks otherwise.*
3. **Prompt-content policy**: confirmed *we never ship the prompt body* — only structured fields + hash. **Get explicit product sign-off** because it's an irreversible product stance.
4. **Per-tenant settings UI**: where does the customer paste the DCR config? New page on the dashboard (*Integrations → Microsoft Sentinel*), or via API/CLI?
5. **Alert thresholds**: what triggers a Sentinel incident? *Proposed defaults: any High severity, plus ≥N blocked of same `SensitiveType` from same user within T window.* **Shipped with those defaults** (N=3, T=1h) as `let` statements at the top of each rule query, so a customer can tune them in the portal without rewriting the logic. **Still needs security-PM sign-off** — the defaults are a starting point, not a validated threshold.
6. **ASIM parser**: ship in v1.1 or v2? *Deferred to v2 — the customers who care about ASIM are large enterprises; foundation-segment buyers won't ask in v1.* **Resolved: shipped.** The deferral was a market-timing call rather than a technical one, and it came due once v1.1 was complete.
7. **Sentinel cost transparency**: customers pay Microsoft per GB ingested. Estimate a 80-user foundation: ~150 events/day × ~500 bytes = ~22 MB/year, trivial. *Document this in customer-facing setup notes so they don't worry.*

## 9. Repository layout

Where this landed in the atlas.ai repo:

```
docs/integrations/microsoft-sentinel/
├── spec.md                     ← this doc
├── data-schema.md              ← column-level schema, machine-readable
├── kql-samples.md              ← starter queries for customer SOCs
└── runbooks/
    └── customer-onboarding.md  ← human checklist for the setup wizard
infra/
├── sentinel-customer-setup.bicep       ← customer Azure resources (DCE/DCR/table/role)
├── sentinel-analytic-rules.bicep       ← deploys the scheduled rules
├── sentinel-analytic-rules.json        ← rule definitions (single source of truth)
├── sentinel-workbook.bicep             ← deploys the workbook
└── sentinel-workbook.json              ← workbook body (single source of truth)
backend/app/services/
├── sentinel_schema.py          ← canonical column list + wire validation
├── sentinel_mapping.py         ← PromptEvent → PromptShieldsActivity_CL (pure)
├── sentinel_forwarder.py       ← token, batching, retry, dead-letter, cursor
└── sentinel_service.py         ← seeded preview stream (pre-forwarder mode)
backend/app/models/sentinel_forward.py  ← delivery cursor + dead-letter queue
backend/app/routers/sentinel_connect.py ← connect wizard, status, dead letters
backend/scripts/replay_sentinel_dead_letters.py  ← replay CLI
backend/tests/sentinel_kql.py            ← shared KQL validation helpers
backend/tests/unit/test_sentinel_analytic_rules.py  ← validates rule KQL against the schema
backend/tests/unit/test_sentinel_workbook.py        ← validates workbook KQL against the schema
worker/app/main.py                       ← WORKER_MODE=sentinel_forwarder
frontend/src/components/spm/sentinel/    ← customer-facing setup wizard
```

The forwarder runs inside the existing backend/worker rather than as a separate
`services/sentinel-forwarder/` deployment: it needs the same models, RLS
session helpers, and Fernet key as everything else, and a separate service
would have to re-import all of it for no isolation gain at this scale.
