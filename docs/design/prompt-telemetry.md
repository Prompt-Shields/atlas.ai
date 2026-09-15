# Unified Prompt Telemetry — Design Spec

**Date:** 2026-06-11
**Status:** Backend and dashboard shipped (steps 1–2 of Sequencing below); the client
changes in step 3 live in their own repositories and are tracked there
**Scope:** atlas.ai backend + dashboard, plus client changes in prompt-shields-safari-widget, prompt-shields-macos-widget, and prompt-shields-sdk

## Problem

Three frontend clients capture prompt activity but none of it lands in atlas.ai's database where the dashboard can consume it:

- The **Safari extension** posts PII violations to `/api/v1/policies/violations` (hash + category counts), but clean prompts are never counted, and the violations path is separate from dashboard analytics.
- The **macOS widget** has a complete telemetry client (daily usage rollups, policy violations) pointed at a placeholder base URL with a different wire format (`/api/usage-events`, `/api/people/me`) and no auth wired.
- The **SDK** sends rich per-call events (vendor, model, tokens, cost, PII categories) to its own collector service with its own Postgres — invisible to atlas.ai.

The dashboard needs a single queryable store of prompt activity across all three sources.

## Decisions (made with the user)

1. **Privacy: hash + metadata only.** No raw or redacted prompt text is ever stored. The schema has no text column, so the guarantee is structural.
2. **Dashboard: new Prompt Activity section** with aggregates, not a raw event list.
3. **Architecture: dedicated typed endpoint + table** (not the generic Developer Events JSONB path, not per-client server-side adapters).

## Architecture overview

One unified wire schema, one ingestion endpoint, one typed table. Each client maps its existing telemetry onto the shared schema and retargets its existing flush machinery at atlas.ai. The dashboard reads purpose-built aggregate endpoints over indexed columns.

```
Safari extension ─┐
macOS widget     ─┼─► POST /api/v1/telemetry/prompt-events ─► grc.prompt_events ─► aggregate endpoints ─► /dashboard/prompt-activity
SDK              ─┘        (X-API-Key, batch 1–500)              (RLS, indexed)
```

## Data model

New table `grc.prompt_events` (Alembic migration 019 — 017 and 018 are taken), with the standard tenant RLS policy (`tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid`):

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID FK → grc.tenants | indexed; RLS |
| `source` | enum `prompt_event_source`: `safari_extension`, `macos_widget`, `sdk` | indexed |
| `event_kind` | enum `prompt_event_kind`: `activity`, `violation` | activity = prompt counted; violation = PII/policy hit |
| `app_id` | varchar(120), nullable | `chatgpt`, `claude`, `notion`, … indexed |
| `prompt_hash` | char(64), nullable | SHA-256 hex; when present, validated as exactly 64 lowercase hex chars (matches the Safari reporter's existing backend contract) |
| `action` | enum `prompt_event_action`: `allowed`, `logged`, `redacted`, `flagged`, `blocked`, nullable | `logged` = violation observed but not acted on (matches the Safari reporter's existing `actionTaken` vocabulary) |
| `severity` | new enum `prompt_event_severity`: `low`, `medium`, `high`, `critical`, nullable | superset of what the clients send: Safari reporter sends `low | medium | high`; macOS `PolicySeverity` sends `low | medium | high | critical`. Do NOT reuse `grc.event_severity` (debug/info/warning/error/critical) — that mismatch would skip every client violation row |
| `pii_categories` | JSONB, default `{}` | category → match count, e.g. `{"email": 2, "ssn": 1}` |
| `device_fingerprint` | varchar(100), nullable | joins to `grc.extension_device_heartbeats.device_fingerprint` |
| `user_external_id` | varchar(255), nullable | Auth0 sub or email |
| `session_id` | varchar(120), nullable | |
| `vendor` | varchar(50), nullable | SDK only (`openai`, `anthropic`, …) |
| `model` | varchar(120), nullable | SDK only |
| `tokens_in`, `tokens_out` | integer, nullable | SDK only |
| `estimated_cost_usd` | numeric(12,6), nullable | SDK only |
| `occurrences` | integer, default 1, 1–10000 | supports client-side rollups (macOS daily aggregation) |
| `occurred_at` | timestamptz, server default now() | indexed |
| `api_key_id` | UUID FK → grc.api_keys, nullable | provenance |
| `created_at`, `updated_at` | timestamptz | standard |

Composite indexes: `(tenant_id, occurred_at)`, `(tenant_id, source, occurred_at)`, `(tenant_id, app_id, occurred_at)`, `(tenant_id, event_kind, occurred_at)`.

**There is no prompt-text column.** The ingestion schema uses strict Pydantic validation (`extra="forbid"`) so payloads carrying unexpected fields (e.g. `prompt_text`) are rejected, not silently dropped.

## Ingestion endpoint

`POST /api/v1/telemetry/prompt-events`

- **Auth:** `X-API-Key` header with existing `aigrc_` keys, resolved exactly like `POST /api/v1/extension/heartbeat` (SHA-256 lookup against `grc.api_keys`, must be active; tenant comes from the key).
- **Body:** `{ "events": [PromptEventIn, …] }`, 1–500 items.
- **Per-event validation:** `source` and `event_kind` required; `prompt_hash` 64 hex chars when present; `occurrences` 1–10000; `occurred_at` ISO-8601 defaulting to server now; unknown fields rejected.
- **Semantics:** valid events insert in one transaction; invalid rows are skipped with a reason rather than failing the batch. Response: `{ "ingested": n, "skipped": m, "skipped_reasons": [...] }` (mirrors the Developer Events API contract so client retry logic is uniform).
- **No event-type pre-registration.** This is first-party product data; the schema is the contract. (This is the key difference from the Developer Events API, where unregistered types are skipped.)

Router: `backend/app/routers/telemetry.py`; model: `backend/app/models/prompt_event.py`; schemas: `backend/app/schemas/telemetry.py`.

## Aggregate endpoints (dashboard backend)

All JWT-authenticated through the standard `get_tenant_db_session` dependency (RLS applies). Common query params: `since`, `until`, optional `source`, optional `app_id`.

- `GET /api/v1/telemetry/prompt-activity/summary` → `{ total_prompts, total_violations, violation_rate, active_devices, active_sources }` (`total_prompts` sums `occurrences`; `active_devices` counts distinct `device_fingerprint`).
- `GET /api/v1/telemetry/prompt-activity/timeseries` → daily buckets of `{ date, prompts, violations }`.
- `GET /api/v1/telemetry/prompt-activity/breakdown?by=app|pii_category|source` → ranked `{ key, prompts, violations }` rows. The `pii_category` breakdown unnests the `pii_categories` JSONB and sums counts.

## Dashboard frontend

New page `frontend/src/app/dashboard/prompt-activity/` following the existing dashboard page patterns, with an API client in `frontend/src/lib/telemetry.ts`:

- Stat cards: total prompts, violations, violation rate, active devices.
- Time-series chart of daily prompts vs. violations.
- Breakdown tables: by app, by PII category.
- Source filter tabs: All / Safari / macOS / SDK; date-range picker.

## Client changes

All three clients keep their fail-open guarantee: telemetry errors never block or degrade the user-facing flow.

### Safari extension (`prompt-shields-safari-widget`)

- Retarget `PromptShieldsExtension/lib/violation-reporter.js` to the new endpoint, mapping its existing payload: `promptHash → prompt_hash`, `actionTaken → action` (its full vocabulary `redacted | flagged | blocked | logged` maps 1:1 — `logged` is a first-class enum value, not dropped), `severity → severity` (`low | medium | high`, 1:1), `byCategory → pii_categories`, plus `source: "safari_extension"`, `event_kind: "violation"`, `app_id` from the existing hostname → platform mapping, `device_fingerprint` from the existing heartbeat fingerprint in `chrome.storage.local`. Switch the auth header from `Authorization: Bearer <apiKey>` to `X-API-Key` to match the new endpoint.
- Add **activity events**: when a prompt passes prescan cleanly (or after the user resolves issues and submits), enqueue `{ event_kind: "activity", action: "allowed" | "redacted", app_id, prompt_hash }`. Batch identical (app_id, action) pairs within a flush window using `occurrences`.
- Reuse existing infrastructure unchanged: offline queue, `atlas.apiKey` config key, 5-minute `chrome.alarms` flush.

### macOS widget (`prompt-shields-macos-widget`)

- Point `TelemetryClient` at the atlas base URL (existing `aiSPMDashboardURL` UserDefaults key) and add an `X-API-Key` header provider. Note: `HTTPTelemetryTransport` already supports an optional `bearerTokenProvider` that is never supplied — the new endpoint needs an `X-API-Key` header instead, so add a parallel API-key provider rather than reusing the Bearer path. The API key is stored in the macOS Keychain (alongside the existing Auth0 credentials in `KeychainCredentialStorage`), not UserDefaults.
- New mapper (`AtlasPromptEventEncoder`) translating existing types:
  - Daily `UsageEvent` rollups → `activity` events: `promptCount` becomes `occurrences` on an `allowed` event; `redactedCount`/`flaggedCount`/`blockedCount` become separate events with the corresponding `action`. Blocked and flagged rollups get `event_kind: "violation"`; redacted rollups remain `event_kind: "activity"` (consistent with the Safari treatment of redaction as resolved activity).
  - `PolicyViolation` posts → `violation` events with `prompt_hash`, `severity` (`PolicySeverity` maps 1:1 — `low | medium | high | critical`), detector category mapped into `pii_categories`, and `action` mapped from `ActionType`: `block → blocked`, `flag → flagged`, `log → logged`, `redact → redacted`; all other `ActionType` values (`rewrite`, `notify`, `require_review`, `evaluated`) map to `null` — do not invent new enum values for them.
  - `source: "macos_widget"`, `app_id` from the monitored-app registry ID, `user_external_id` from the Auth0 sub.
- Existing 5-minute flush, retry/backoff, and aggregation machinery unchanged.

### SDK (`prompt-shields-sdk`)

- Add an optional atlas sink to `packages/sdk/prompt_shields/telemetry.py`: config `atlas_url` + `atlas_api_key` (env: `PS_ATLAS_URL`, `PS_ATLAS_API_KEY`). When configured, each LLM call also emits one event to atlas: `source: "sdk"`, `event_kind: "activity"`, `vendor`, `model`, `tokens_in/out`, `estimated_cost_usd`, `session_id`, `user_external_id ← user_id`, `pii_categories` from `detect_pii_categories()` (which returns a `list[str]` — the sink maps each detected category to count `1`; it does not invent per-category counts), and `prompt_hash` (SHA-256 of the concatenated message contents).
- **`prompt_text` is never sent to atlas**, regardless of the collector-side `send_prompt_text` setting. The collector integration is unchanged (it still serves the AI-registry features).
- Reuses the existing buffer/flush/fail-open machinery; the atlas sink is a second destination, not a replacement.

## Error handling

- Clients: fail-open, queue locally, retry with backoff, drop oldest on overflow (all existing behavior).
- Server: per-row skip-with-reason; whole-request 401 only for bad/inactive API key; 422 only for structurally invalid envelopes.
- Hash validation failures are skipped rows with an explicit reason, preserving the Safari reporter's existing "hard reject non-64-hex" contract at row granularity.

## Testing

- **Backend:** unit tests for ingestion validation (hash format, occurrences bounds, unknown-field rejection, batch skip semantics); RLS isolation tests for `grc.prompt_events` following `backend/tests/tenancy/test_rls_enforced.py`; aggregate endpoint tests with seeded fixtures (summary math, timeseries bucketing, JSONB category unnesting).
- **Safari extension:** Jest tests for the payload mapper and occurrence batching.
- **macOS widget:** XCTest for `AtlasPromptEventEncoder` mappings (rollup → occurrences, violation → categories).
- **SDK:** pytest for the atlas sink — event shape, hash determinism, assertion that `prompt_text` never appears in atlas-bound payloads even when `send_prompt_text=True`.

## Sequencing

1. atlas.ai backend: migration 019, model, ingestion endpoint, aggregate endpoints (everything else depends on this contract).
2. Dashboard: API client + Prompt Activity page.
3. Clients, in parallel once the contract is live: Safari extension, macOS widget, SDK.

## Out of scope

- Raw or redacted prompt text storage (explicitly decided against).
- Migrating the SDK collector's registry/asset features into atlas.
- Replacing the existing `/api/v1/policies/violations` flow for policy enforcement (this spec adds the analytics path; consolidating the two is a possible follow-up).
- Tenant-configurable text-logging policy (rejected for now; revisit only with a concrete compliance-reviewed requirement).
