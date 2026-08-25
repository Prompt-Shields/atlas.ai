# `PromptShieldsActivity_CL` — column schema

Custom log table for the Microsoft Sentinel integration. Companion to [spec.md](spec.md). Machine-readable JSON column array at the bottom of this file is the canonical source — the Bicep template and the forwarder's outbound payload schema are both derived from it.

## Conventions

- Column names are **PascalCase** to match Sentinel built-in tables (`SigninLogs`, `OfficeActivity`).
- Suffix `_CL` on the table name is the Log Analytics convention for custom logs; preserved for DCR-based custom tables.
- Stream name (declared in the DCR) is `Custom-PromptShields_v1`. The `_v1` suffix lets us evolve schema later without breaking customer queries.
- Time columns are ISO-8601 strings on the wire; Sentinel parses them to `datetime` on ingest.
- `null` is allowed wherever marked **nullable**; do not send empty strings.

## Columns

| Column | Type | Required | Source | Notes |
|---|---|---|---|---|
| `TimeGenerated` | datetime | yes | event timestamp | Required by Sentinel for *every* table; equals the moment the redaction/coach/block fired client-side. |
| `EventId` | string | yes | Prompt Shields | Stable unique ID, e.g. `EV-1041`. Used for idempotent send / dedup verification. |
| `TenantId` | string | yes | Prompt Shields | Prompt Shields tenant — **not** the customer's Azure AD tenant. Lets Prompt Shields multi-tenant data co-exist if a customer ingests multiple Prompt Shields tenants into one workspace. |
| `User` | string | yes | OS / SSO | UPN if available (`alex.morgan@example.org`), else display name. |
| `UserAadObjectId` | string | nullable | Entra | Denormalized so KQL `join (SigninLogs) on $left.UserAadObjectId == $right.UserId` works without a lookup table. |
| `Department` | string | nullable | Prompt Shields directory | Free-text department; matches the customer's directory mapping. |
| `AiTool` | string | yes | Prompt Shields | One of: `Microsoft Copilot Premium`, `ChatGPT Business`, `Claude`, `Gemini`, `Perplexity`, or another. Free-form to allow future tools without a schema bump. |
| `IsShadowAi` | bool | yes | Prompt Shields | True if the tool is unsanctioned by the customer's AI tool registry. |
| `EventType` | string | yes | Prompt Shields | Enum: `Redacted`, `Anonymised`, `Blocked`, `Coached`, `BiasFlagged`. |
| `SensitiveType` | string | nullable | Prompt Shields | Free-form: `SSN+EIN`, `Compensation`, `PHI`, `Donor list`, `Banking`, `Protected characteristics`, etc. `null` for `Coached` events that weren't tied to a sensitive type. |
| `Severity` | string | yes | Prompt Shields | Enum: `Low`, `Medium`, `High`. |
| `Detail` | string | yes | Prompt Shields | Short structured description, e.g. `"Beneficiary home address + medical condition in grant memo"`. **Never the prompt body.** |
| `PolicyId` | string | nullable | Prompt Shields | The policy that fired, if any. |
| `PolicyName` | string | nullable | Prompt Shields | Denormalized for KQL ergonomics. |
| `EndpointId` | string | nullable | Intune | Device ID. Lets KQL join with `IntuneDevices` / `Heartbeat`. |
| `EndpointPlatform` | string | nullable | Prompt Shields agent | `Windows ARM 64`, `Windows x64`, `macOS`. |
| `RedactionTokenCount` | int | nullable | Prompt Shields | Number of tokens redacted (only meaningful for `Redacted` / `Anonymised` events). |
| `PromptHash` | string | yes | Prompt Shields | SHA-256 hex of the original prompt. Lets the customer correlate the same prompt appearing in different events without us shipping content. |

## Explicit non-goals

- **No prompt body.** Not in `Detail`, not in any other column, not as a side-channel. Confirm with product before any change to this rule.
- **No raw response from the model.** Same reason.
- **No customer secrets, tokens, or credentials**, even if extracted from the prompt — those go through a different incident-response path, not Sentinel telemetry.

## Stream / table mapping in the DCR

The DCR's `dataFlows` block maps the inbound stream to the destination table:

```jsonc
{
  "streams": ["Custom-PromptShields_v1"],
  "destinations": ["<workspace-name>"],
  "transformKql": "source",
  "outputStream": "Custom-PromptShieldsActivity_CL"
}
```

If the customer wants a transform at ingest (e.g. to drop low-severity events to save cost), they can edit `transformKql`. Default is pass-through.

## JSON schema (canonical)

```json
{
  "tableName": "PromptShieldsActivity_CL",
  "streamName": "Custom-PromptShields_v1",
  "columns": [
    { "name": "TimeGenerated",       "type": "datetime", "required": true  },
    { "name": "EventId",             "type": "string",   "required": true  },
    { "name": "TenantId",            "type": "string",   "required": true  },
    { "name": "User",                "type": "string",   "required": true  },
    { "name": "UserAadObjectId",     "type": "string",   "required": false },
    { "name": "Department",          "type": "string",   "required": false },
    { "name": "AiTool",              "type": "string",   "required": true  },
    { "name": "IsShadowAi",          "type": "boolean",  "required": true  },
    { "name": "EventType",           "type": "string",   "required": true  },
    { "name": "SensitiveType",       "type": "string",   "required": false },
    { "name": "Severity",            "type": "string",   "required": true  },
    { "name": "Detail",              "type": "string",   "required": true  },
    { "name": "PolicyId",            "type": "string",   "required": false },
    { "name": "PolicyName",          "type": "string",   "required": false },
    { "name": "EndpointId",          "type": "string",   "required": false },
    { "name": "EndpointPlatform",    "type": "string",   "required": false },
    { "name": "RedactionTokenCount", "type": "int",      "required": false },
    { "name": "PromptHash",          "type": "string",   "required": true  }
  ]
}
```

## Sample payload (single event)

```json
{
  "TimeGenerated": "2026-05-05T09:14:00Z",
  "EventId": "EV-1041",
  "TenantId": "ps-tenant-weinberg-01",
  "User": "l.park@example.org",
  "UserAadObjectId": "f8b2c0e3-1a2d-4e5f-9a8b-7c6d5e4f3210",
  "Department": "Grants Management",
  "AiTool": "ChatGPT Business",
  "IsShadowAi": false,
  "EventType": "Redacted",
  "SensitiveType": "SSN+EIN",
  "Severity": "Medium",
  "Detail": "Pasted grantee SSN + EIN drafting MOU",
  "PolicyId": "POL-001",
  "PolicyName": "No grantee PII to external LLMs",
  "EndpointId": "intune-device-9b3a1f2c",
  "EndpointPlatform": "Windows ARM 64",
  "RedactionTokenCount": 14,
  "PromptHash": "a3f1c…"
}
```

## Schema versioning

`Custom-PromptShields_v1` is the wire-stream version; bumps to `_v2` only on **breaking** changes. Adding a nullable column = update the DCR, no version bump. Renaming or removing a column = `_v2` and a deprecation window where forwarder writes both streams. Customers' KQL references `PromptShieldsActivity_CL` (the table name, not the stream), so additive changes are invisible to their existing queries.
