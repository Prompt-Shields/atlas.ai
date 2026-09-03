# Reporting AI spend from your own apps

For AI your organisation runs itself — an app on **Azure AI Foundry**, on **AWS
Bedrock**, or a model you host — rather than a vendor account atlas.ai can
connect to.

**Why this one is different.** Every other cost connector pulls: you paste an
API key, we call the vendor's billing API nightly. That is impossible here.
Tokens your app burns on Foundry or Bedrock are billed to *your* Azure or AWS
subscription, aggregated with everything else on it and undifferentiated by
application. There is no per-app cost endpoint to read. So the direction
inverts: **your app reports its own usage**, and atlas.ai derives the cost.

There is nothing to connect in the dashboard. The integration tile appears
"Connected" once your first batch arrives.

---

## The endpoint

```
POST /api/v1/cost/usage
X-API-Key: <your tenant API key>
Content-Type: application/json
```

The API key decides which tenant the spend lands in. The payload cannot name a
tenant, and a key that is not tenant-scoped is rejected with `403`.

### Request

```json
{
  "batch_id": "2026-08-31T14:00Z-checkout-svc-0042",
  "provider": "azure_ai_foundry",
  "records": [
    {
      "model": "gpt-4o-2024-08-06",
      "tokens_in": 1820,
      "tokens_out": 340,
      "occurred_at": "2026-08-31T13:58:11Z",
      "app_id": "checkout-svc"
    },
    {
      "model": "gpt-4o-mini",
      "tokens_in": 512,
      "tokens_out": 96,
      "occurred_at": "2026-08-31T13:59:02Z",
      "app_id": "checkout-svc"
    }
  ]
}
```

| Field | Required | Notes |
|---|---|---|
| `batch_id` | yes | Your idempotency key, ≤200 chars. See [Retries](#retries-and-batch_id) — this is the field that stops a retry double-charging you. |
| `provider` | yes | `azure_ai_foundry`, `aws_bedrock`, or `self_hosted`. |
| `records` | yes | 1–1000 per request. |
| `records[].model` | yes | Your deployment id or model name; normalised on our side (see [Pricing](#pricing-and-unpriced-models)). |
| `records[].tokens_in` / `tokens_out` | no (default 0) | Non-negative. |
| `records[].occurred_at` | no | Defaults to now. Naive timestamps read as UTC. |
| `records[].app_id` | no | Free-text label kept for your own attribution. Never a ledger key. |

Unknown fields are **rejected**, not ignored — a typo'd field name is a bug you
want to hear about, and it forecloses prompt text ever arriving here by
accident. Nothing about the content of a prompt belongs in this endpoint.

### Response

```json
{
  "batch_id": "2026-08-31T14:00Z-checkout-svc-0042",
  "accepted_calls": 2,
  "skipped_calls": 0,
  "rows_touched": 1,
  "cost_usd": "0.008150",
  "unpriced_models": [],
  "duplicate": false
}
```

`rows_touched` is the number of daily ledger rows the batch updated — per-call
records roll up to one row per (day, model), so it is normally small.

---

## Retries and `batch_id`

**Read this before you write the retry logic.**

A pull connector re-fetches a whole day and overwrites what it finds, so
running it twice is harmless. A pushed batch carries only the calls since your
last push, so it must **accumulate** into the day's total — and accumulation is
not idempotent. A client that times out, retries, and gets through on the
second attempt would add the same tokens twice. The doubled figure still looks
plausible, so nobody catches it.

So `batch_id` is required. Send the same id on every retry of the same batch
and the replay is a no-op: you get the original result back with
`"duplicate": true`, and nothing is added.

- Generate the id **before** the first attempt, not per attempt.
- Make it unique per batch. Ids are scoped to your tenant, so you cannot
  collide with another customer.
- Reusing an id for *different* records silently discards them — the batch is
  treated as a replay. Never reuse.

If a request fails without a response, retry with the same id. That is always
safe.

---

## Pricing and unpriced models

You send tokens; we compute dollars from a price book and tag the result
`derived_tokens`. The dashboard badges it as an estimate, because the
authoritative number is on your cloud bill. Model names are normalised, so
`anthropic.claude-sonnet-4-v1:0`, `claude-sonnet-4`, and a Foundry deployment
alias ending in a release date all land on the same key.

**An unpriced model is never costed at zero.** Zero on a spend dashboard reads
as "this model is free" rather than "we don't know what this costs", and it
understates your real spend — the exact failure a cost tool exists to prevent.
Instead the calls are accepted, the tokens recorded, and the model name comes
back in `unpriced_models`.

**Treat a non-empty `unpriced_models` as an alert.** It means spend is being
under-reported until a price is configured. Set your own prices per integration
(USD per **one million** tokens, matching how vendors publish them):

```json
{
  "config_json": {
    "price_book": {
      "gpt-4o": { "input_per_mtok": "2.50", "output_per_mtok": "10.00" },
      "our-finetune-v3": { "input_per_mtok": "1.10", "output_per_mtok": "3.30" }
    }
  }
}
```

`PATCH /api/v1/integrations/{integration_id}`. Your entries override the
built-in list prices, which is also how you apply negotiated or regional rates
rather than living with ours.

---

## What lands in the ledger

Calls roll up to the ledger's existing daily grain:

| Ledger field | Value |
|---|---|
| `usage_date` | UTC day of `occurred_at` |
| `subject_kind` | `model` |
| `subject_ref` | the normalised model name |
| `cost_kind` | `tokens` |
| `cost_source` | `derived_tokens` |

Individual calls are **not** stored. This is a spend ledger, not a request
tracing tool — if you need per-call records, they belong in your own
observability stack.

---

## Batching guidance

Buffer in your app and push periodically; a request per model call wastes far
more than it measures. A batch every minute, or every 1000 calls, is a
reasonable starting point — 1000 records is the per-request limit.

Late records are fine. `occurred_at` decides the day, so a batch that arrives
after midnight still lands on the day the calls happened.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `403 API_KEY_NOT_TENANT_SCOPED` | The key is not scoped to a tenant. | Issue a tenant-scoped key. |
| `422` naming an unexpected field | Unknown fields are rejected. | Check the field name against the table above. |
| `"duplicate": true` on a batch you meant as new | The `batch_id` was already used. | Generate a fresh id per batch; never reuse. |
| `cost_usd` is `0` but calls were accepted | Every model in the batch is unpriced. | Check `unpriced_models` and add prices. |
| Spend looks roughly doubled | Retries without a stable `batch_id`. | Generate the id before the first attempt. |
| Tile still says "Not connected" | No batch has arrived. | The row is created by the first successful push. |

---

## Not built yet

The open-source telemetry library. The endpoint above is the contract, and
today you integrate against it directly — a few lines around your model client.
