# Starter KQL for `PromptShieldsActivity_CL`

Queries a customer SOC can paste into Microsoft Sentinel once the forwarder is
delivering. Companion to [spec.md](spec.md) and
[data-schema.md](data-schema.md).

All of these run against the custom **table** (`PromptShieldsActivity_CL`), not
the wire stream (`Custom-PromptShields_v1`). That is deliberate: additive
schema changes bump the stream, never the table, so queries written today keep
working. See "Schema versioning" in data-schema.md.

> **No prompt content.** No query here can return the text of a prompt, because
> no column holds it. `PromptHash` correlates the *same* prompt across events
> without revealing it.

---

## Smoke test — is the pipe flowing?

The first thing to run after finishing the setup wizard.

```kql
PromptShieldsActivity_CL
| where TimeGenerated > ago(24h)
| summarize Events = count() by EventType, Severity
| order by Events desc
```

Empty result and the forwarder reports success? The tenant may simply have had
no policy-relevant activity in the window — ordinary allowed prompts are not
forwarded. Widen to `ago(7d)` before treating it as a fault.

## Volume over time

```kql
PromptShieldsActivity_CL
| where TimeGenerated > ago(30d)
| summarize Events = count() by bin(TimeGenerated, 1d), EventType
| render timechart
```

---

## Shadow AI

Unsanctioned tools, ranked by how much sensitive material went into them.

```kql
PromptShieldsActivity_CL
| where TimeGenerated > ago(7d)
| where IsShadowAi
| summarize
    Events = count(),
    People = dcount(User),
    Blocked = countif(EventType == "Blocked")
    by AiTool
| order by Events desc
```

First appearance of a tool nobody has seen before — a good scheduled-rule
candidate:

```kql
let known = PromptShieldsActivity_CL
    | where TimeGenerated between (ago(30d) .. ago(1d))
    | distinct AiTool;
PromptShieldsActivity_CL
| where TimeGenerated > ago(1d)
| where IsShadowAi
| where AiTool !in (known)
| summarize FirstSeen = min(TimeGenerated), Events = count() by AiTool, User
```

## High-severity activity

Until the Graph Security alerts channel ships (v1.1), this is how high-severity
events reach the analyst queue — as a scheduled analytics rule.

```kql
PromptShieldsActivity_CL
| where TimeGenerated > ago(1h)
| where Severity == "High"
| project TimeGenerated, User, Department, AiTool, EventType, SensitiveType, Detail
| order by TimeGenerated desc
```

Repeated blocks of the same sensitive type by one person — the pattern the spec
proposes as a default alert trigger (§8, open question 5):

```kql
PromptShieldsActivity_CL
| where TimeGenerated > ago(1h)
| where EventType == "Blocked"
| summarize Attempts = count() by User, SensitiveType, bin(TimeGenerated, 1h)
| where Attempts >= 3
```

## Bias-flagged prompts

```kql
PromptShieldsActivity_CL
| where TimeGenerated > ago(30d)
| where EventType == "BiasFlagged"
| summarize Events = count() by Department, AiTool
| order by Events desc
```

---

## Correlating with Microsoft's own tables

`UserAadObjectId` is denormalised onto every row precisely so these joins need
no lookup table.

**With Entra sign-ins** — where was this person signing in from around the time
they pasted something sensitive?

```kql
PromptShieldsActivity_CL
| where TimeGenerated > ago(24h)
| where Severity == "High"
| where isnotempty(UserAadObjectId)
| join kind=inner (
    SigninLogs
    | where TimeGenerated > ago(24h)
    | project SigninTime = TimeGenerated, UserId, IPAddress, Location, AppDisplayName
) on $left.UserAadObjectId == $right.UserId
| where abs(datetime_diff('minute', SigninTime, TimeGenerated)) <= 60
| project TimeGenerated, User, AiTool, EventType, SensitiveType, IPAddress, Location
```

**By endpoint** — which devices produce the most high-severity activity?

```kql
PromptShieldsActivity_CL
| where TimeGenerated > ago(7d)
| where isnotempty(EndpointId)
| summarize Events = count(), HighSev = countif(Severity == "High") by EndpointId, EndpointPlatform
| order by HighSev desc
```

> **Caveat.** `EndpointId` is currently the Prompt Shields agent's own device
> id, not the Intune device id, so it does **not** yet join `IntuneDevices` or
> `Heartbeat`. It is stable and unique per device, so it groups a customer's
> own events correctly. Populating it with the MDM-side id depends on the
> managed-device unification work; until then, pivot to Intune via `User` /
> `UserAadObjectId` instead.

---

## Prompt-hash correlation

The same prompt reaching several tools, or several people — visible without any
prompt content leaving Prompt Shields.

```kql
PromptShieldsActivity_CL
| where TimeGenerated > ago(30d)
| summarize
    Tools = make_set(AiTool),
    People = dcount(User),
    Occurrences = count()
    by PromptHash
| where Occurrences > 1
| order by Occurrences desc
```

## Redaction volume

How much sensitive material the shield is actually catching.

```kql
PromptShieldsActivity_CL
| where TimeGenerated > ago(30d)
| where EventType in ("Redacted", "Anonymised")
| summarize Spans = sum(RedactionTokenCount), Events = count() by SensitiveType
| order by Spans desc
```

---

## Multi-tenant workspaces

A customer ingesting more than one Prompt Shields tenant into a single
workspace filters on `TenantId` — the **Prompt Shields** tenant, not their
Azure AD tenant.

```kql
PromptShieldsActivity_CL
| where TimeGenerated > ago(7d)
| summarize Events = count() by TenantId, EventType
```

---

## Cost sanity check

Customers pay Microsoft per GB ingested. This shows what the connector is
actually costing them.

```kql
Usage
| where TimeGenerated > ago(30d)
| where DataType == "PromptShieldsActivity_CL"
| summarize IngestedMB = sum(Quantity) by bin(TimeGenerated, 1d)
| render timechart
```
