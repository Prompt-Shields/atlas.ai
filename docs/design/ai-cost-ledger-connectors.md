# AI Cost Ledger + Vendor Connectors — Design Spec

**Date:** 2026-06-17
**Status:** Approved design, pre-implementation
**Scope:** atlas.ai backend + dashboard. First slice of a larger "AI Spend & ROI" product surface.

## Problem

atlas.ai customers spend money on AI across many vendors — Anthropic, OpenAI, Cursor, GitHub Copilot, Vercel, plus their own homebrew apps on Azure AI Foundry / AWS Bedrock — but that spend is scattered across each vendor's billing console. There is no single place a tenant can answer "what is our total AI spend, by vendor, by model, over time?"

This slice delivers the **spine**: a unified, per-tenant **cost ledger** plus a **pluggable connector framework** that pulls actual spend from external vendor APIs on a daily cadence. Two later slices build on this ledger:

- **Slice 2** — self-hosted app instrumentation (open-source telemetry lib → ledger).
- **Slice 3** — the human-vs-AI **ROI** model and dashboard (the headline value).

Neither slice 2 nor slice 3 is in scope here. They are named only to fix the ledger's shape so it does not need to be re-cut later.

## What already exists (and is reused, not rebuilt)

- **`Integration` model** (`backend/app/models/integration.py`) — one row per (tenant, provider), with Fernet-encrypted credentials (`access_token_encrypted` / `refresh_token_encrypted`), a non-secret `config_json`, a `status` enum, and `external_id`/`external_name`. The `IntegrationProvider` enum already enumerates AWS / AWS_BEDROCK / GCP_VERTEX placeholders.
- **Crypto** (`backend/app/services/crypto.py`) — Fernet/`MultiFernet` token encryption keyed from env. New connector keys use this same path; no new secrets mechanism.
- **`integration_registry.py`** — static `ProviderMeta` per provider that the `/dashboard/integrations` grid renders. New cost providers register here.
- **`*_sync.py` adapter family** (`intune_sync`, `jamf_sync`, `jumpcloud_sync`, `purview_sync`, `defender_sync`, …) — the established per-provider sync-adapter shape that the cost connectors mirror.
- **Tenancy** — every tenant-scoped row carries `tenant_id` and is protected by the standard Postgres RLS policy `tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid`.

## Decisions (made with the user)

1. **Audience: multi-tenant product feature** — each tenant connects its own vendor accounts and sees its own spend. Connector framework + per-tenant encrypted credential storage + RLS throughout.
2. **First slice: ledger + vendor connectors.** ROI and self-hosted instrumentation are deferred to later slices.
3. **v1 connectors (all five):** Anthropic, OpenAI API, Cursor, GitHub Copilot, Vercel. The **connector framework is the real deliverable**; each vendor is a pluggable adapter conforming to one interface. Anthropic is built end-to-end first to prove the interface.
4. **Ledger grain: daily aggregate**, because that is what every vendor cost API actually returns. Per-call grain is a concern only for slice 2 (self-hosted instrumentation) and is handled there.
5. **Cost source of truth: prefer the vendor's reported dollars; derive only when forced, and label the derivation.** A `cost_source` enum records provenance so the dashboard can badge actual vs estimated spend.
6. **Scheduling: external cron → authenticated internal endpoint.** No in-app scheduler exists; an Azure Container Apps Job / GitHub Action hits a daily sync endpoint. Plus a per-integration manual "Sync now."
7. **Currency: USD only for v1.** All five vendors report USD. Multi-currency normalization is out of scope.

## Architecture overview

```
            ┌─ Anthropic Admin Cost/Usage API ─┐
            ├─ OpenAI Org Costs/Usage API ──────┤
 daily cron │  Cursor Teams Admin API           │   CostConnector adapters
   or       ├─ GitHub Copilot Billing/Metrics ──┤──► (one per vendor) ──► upsert
"Sync now"  └─ Vercel Billing / AI Gateway ─────┘        │                    │
                                                          ▼                    ▼
                                        normalize → CostRecord        grc.ai_cost_records
                                                                       (daily grain, RLS)
                                                                              │
                                                                  aggregate endpoints
                                                                              │
                                                                  /dashboard/ai-spend
```

One ledger table. One adapter interface. One sync orchestrator. The dashboard reads purpose-built aggregate endpoints over indexed columns.

## Data model

### New enums (`grc` schema)

- `cost_provider`: `anthropic`, `openai`, `cursor`, `github_copilot`, `vercel`.
- `cost_kind`: `metered_usage`, `seat_subscription`, `infra`.
- `cost_subject_kind`: `model`, `member`, `sku`, `other`.
- `cost_source`: `vendor_reported`, `derived_tokens`, `derived_seats`.

These are **new** enums — do not reuse `IntegrationProvider` for `cost_provider` (the cost connectors are a curated subset and the dashboard groups by it; a separate enum keeps the ledger decoupled from the broader integration catalog). The connected-account credential still lives on an `Integration` row whose `provider` is the matching `IntegrationProvider` value (new values `ANTHROPIC`, `OPENAI`, `CURSOR`, `GITHUB_COPILOT`, `VERCEL` are added to that enum too).

### New table `grc.ai_cost_records` (Alembic migration — next free revision)

Standard tenant RLS policy applies.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID FK → grc.tenants | indexed; RLS |
| `integration_id` | UUID FK → grc.integrations | provenance; `ondelete=CASCADE` |
| `provider` | enum `cost_provider` | indexed |
| `usage_date` | date | the day the cost was incurred; indexed |
| `cost_kind` | enum `cost_kind` | |
| `subject_kind` | enum `cost_subject_kind`, nullable | |
| `subject_ref` | varchar(255), nullable | e.g. `claude-opus-4`, member email, sku id; `""` (empty string, never NULL) when the vendor gives no subject — keeps the unique key total |
| `tokens_in` | bigint, nullable | metered_usage only |
| `tokens_out` | bigint, nullable | metered_usage only |
| `seats` | integer, nullable | seat_subscription only |
| `quantity` | numeric(18,4), nullable | generic unit count (e.g. Copilot usage-metric counts) |
| `cost_usd` | numeric(14,6) | the money |
| `cost_source` | enum `cost_source` | actual vs estimated provenance |
| `is_provisional` | boolean, default false | current/partial day; overwritten on next pull |
| `raw_metadata` | JSONB, default `{}` | vendor row echo for audit/debug (no secrets) |

**PII note:** when `subject_kind=member`, `subject_ref` holds a vendor-side member identifier (typically an email). This is intentional — per-member spend is a product requirement (Cursor, Copilot) — but it is PII. The plan must treat `subject_ref` as tenant-scoped PII: covered by RLS like every other column, excluded from any cross-tenant aggregate, and never logged in plaintext. `raw_metadata` remains secret-free (no keys/tokens).
| `ingested_at` | timestamptz, server default now() | when this row was last written by a sync |
| `created_at`, `updated_at` | timestamptz | standard |

**Idempotent upsert key (unique constraint):** `(tenant_id, integration_id, usage_date, cost_kind, subject_kind, subject_ref)`. Re-pulling a day performs an `ON CONFLICT … DO UPDATE` (Postgres upsert) of `tokens_*`, `seats`, `quantity`, `cost_usd`, `cost_source`, `is_provisional`, `raw_metadata`, `ingested_at` — never inserting a duplicate. Because `subject_ref` defaults to `""` rather than NULL, the unique constraint always applies (NULLs would defeat it in Postgres).

**Indexes:** the unique constraint above, plus `(tenant_id, usage_date)` and `(tenant_id, provider, usage_date)`.

### Per-tenant price book (for derived costs)

When a vendor exposes usage/seats but not dollars (e.g. Copilot), the adapter derives cost from a **price book**. v1 stores this as non-secret JSON in the connector's `Integration.config_json`, e.g. `{"copilot_seat_price_usd": 19.00}`, with a hard-coded default in the adapter when unset. A dedicated price-book table is explicitly **not** built in v1 (YAGNI); it can replace the config_json field later without touching the ledger.

## Connector framework

### Adapter interface

`backend/app/services/cost/base.py` defines:

```python
class CostRecord(TypedDict):  # adapter output, pre-persistence
    usage_date: date
    cost_kind: CostKind
    subject_kind: CostSubjectKind | None
    subject_ref: str            # "" when none
    tokens_in: int | None
    tokens_out: int | None
    seats: int | None
    quantity: Decimal | None
    cost_usd: Decimal
    cost_source: CostSource
    is_provisional: bool
    raw_metadata: dict

class CostConnector(Protocol):
    provider: CostProvider
    def fetch_cost(self, integration: Integration, since: date, until: date) -> list[CostRecord]: ...
```

One adapter per vendor under `backend/app/services/cost/` (`anthropic_cost.py`, `openai_cost.py`, `cursor_cost.py`, `copilot_cost.py`, `vercel_cost.py`). A registry (`cost_connector_registry.py`) maps `CostProvider → CostConnector`, mirroring `integration_registry`.

The adapter is responsible only for **fetch + normalize to `CostRecord`**. It never touches the DB. Persistence/upsert is the orchestrator's job, so adapters are pure and unit-testable against recorded vendor responses.

### Per-vendor mapping

- **Anthropic** — Admin Cost Report (`/v1/organizations/cost_report`) → `metered_usage`, `subject_kind=model`, `cost_source=vendor_reported`. Admin Usage Report fills `tokens_in/out`. Requires an `sk-ant-admin…` key in `access_token_encrypted`; org id (if needed) in `config_json`.
- **OpenAI API** — Org Costs API → `metered_usage`, `vendor_reported`; Usage/Completions API fills tokens. Admin key in `access_token_encrypted`. **This is OpenAI _API_ spend, not ChatGPT consumer/Team seats** — that weaker connector is out of scope.
- **Cursor** — Teams Admin API daily usage + spend → per-`member` rows, `cost_source=vendor_reported`; `cost_kind=metered_usage` for usage-based spend. Admin key in `access_token_encrypted`; team id in `config_json`.
- **GitHub Copilot** — Billing/seat API gives seat counts (no per-day dollars) → `seat_subscription`, `subject_kind=member` or `sku`, `cost_source=derived_seats` (seats × price-book price). Metrics API usage counts stored in `quantity`. Token (PAT/app) in `access_token_encrypted`; org slug in `config_json`.
- **Vercel** — Billing/usage API → `infra`; or AI Gateway token spend → `metered_usage`. Which surface(s) a tenant enables is a `config_json` flag. Token in `access_token_encrypted`.

Per-vendor API endpoint details and pagination are an implementation concern for the plan; this spec fixes the normalization contract, not the wire calls.

### Sync orchestrator

`backend/app/services/cost/cost_sync_service.py`:

1. For a given tenant (or all tenants, in the cron case), load `Integration` rows whose `provider` is a cost provider and `status=CONNECTED`.
2. For each, resolve the adapter from the registry, decrypt the key, call `fetch_cost(integration, since, until)`.
3. Upsert returned `CostRecord`s into `grc.ai_cost_records` (single transaction per integration) using the idempotent key.
4. On success: `Integration.status=CONNECTED`, clear `last_error`. On failure: `status=ERROR`, store the reason in the **existing** `Integration.last_error` column (`String(500)` — truncate the adapter's reason to fit), and **continue to the next connector** — one bad vendor never blocks the others. No new error column or migration is needed.

**Window:** the daily run pulls `[today - 2 days, today]` (a small overlap re-pulls recently-finalized days; the upsert makes this safe). `today`'s rows are written with `is_provisional=true`. **Backfill:** on first connect, pull the last 90 days (or the vendor's max lookback if shorter); `log` the actual window covered so a shorter-than-requested backfill is visible, not silent.

## Endpoints

### Sync (write)

- `POST /api/v1/cost/integrations/{integration_id}/sync` — JWT-authed "Sync now" for one connected integration (current tenant). Returns `{ records_upserted, window: {since, until}, status }`.
- `POST /api/v1/cost/sync` — **cron entry point**, authenticated by a shared secret header (`X-Cron-Secret`, env-configured), iterates all tenants' connected cost integrations. Returns a per-integration summary. This endpoint does **not** use the JWT/RLS session; it sets `app.current_tenant_id` per tenant inside the loop so RLS still applies to writes.

### Aggregates (read — JWT + RLS via `get_tenant_db_session`)

Common params: `since`, `until`, optional `provider`. All scoped to the caller's tenant by RLS.

- `GET /api/v1/cost/summary` → `{ total_cost_usd, by_source: {vendor_reported, derived}, active_connectors, provisional_cost_usd }`.
- `GET /api/v1/cost/timeseries` → daily buckets `{ date, cost_usd, is_provisional }`.
- `GET /api/v1/cost/breakdown?by=provider|model|member` → ranked `{ key, cost_usd, cost_source }` rows.

Router: `backend/app/routers/cost.py`. Model: `backend/app/models/ai_cost_record.py`. Schemas: `backend/app/schemas/cost.py`. Adapters/registry/orchestrator: `backend/app/services/cost/`.

## Dashboard frontend

New page `frontend/src/app/dashboard/ai-spend/`, following the existing dashboard page pattern (e.g. the planned prompt-activity page), with an API client `frontend/src/lib/cost.ts`:

- Stat cards: total spend, actual vs estimated split, active connectors, provisional (today, partial) spend.
- Time-series chart of daily spend, with provisional days visually distinct.
- Breakdown tables: by vendor, by model/member.
- An **actual-vs-estimated badge** driven by `cost_source` so derived figures (e.g. Copilot) are never mistaken for billed dollars.

Connecting a cost vendor reuses the existing `/dashboard/integrations` connect flow; the new providers appear as tiles via `integration_registry`.

## Error handling

- **Adapter failure** (network, auth, schema drift) → that integration goes `ERROR` with a stored reason; sync continues for others; the dashboard surfaces the error state on the connector tile.
- **Partial/current day** → rows written `is_provisional=true`; overwritten (not duplicated) on the next pull via the upsert key.
- **Re-pull / overlap** → idempotent by construction (unique key + upsert).
- **Derived cost with no price configured** → adapter falls back to a documented default and tags `cost_source=derived_*`; never silently emits `0`.
- **Cron secret missing/invalid** → `POST /api/v1/cost/sync` returns 401; no work performed.

## Testing

- **Per-adapter unit tests** against recorded/mocked vendor responses (fixtures), asserting correct `CostRecord` normalization including `cost_kind`/`cost_source` tagging.
- **Idempotent-upsert test** — pulling the same day twice yields one row, updated not duplicated.
- **Derived-cost test** — Copilot seats × price-book → expected `cost_usd`, `cost_source=derived_seats`; default-price fallback path.
- **RLS test** — tenant A cannot read tenant B's `ai_cost_records`; cron write loop sets `app.current_tenant_id` correctly per tenant.
- **Endpoint tests** — aggregate math (summary/timeseries/breakdown) and `provisional` handling; cron-secret auth on `POST /api/v1/cost/sync`.

## Out of scope (explicit — future slices)

- Self-hosted app instrumentation via open-source telemetry lib (Azure AI Foundry / AWS Bedrock) → **slice 2**.
- Human-cost model and human-vs-AI **ROI** computation and dashboard → **slice 3**.
- ChatGPT consumer/Team/Enterprise seat connector (weak/limited cost API).
- Multi-currency normalization; budgets/alerts; cost anomaly detection; chargeback/showback allocation.
- A dedicated price-book table (config_json suffices for v1).
