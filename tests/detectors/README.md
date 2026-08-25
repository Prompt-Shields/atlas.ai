# Cross-platform PII detector fixtures

`pii-fixtures.json` is the **canonical test corpus** that every Promptly PII detector implementation must agree on.

## Why this exists

Three detector implementations ship across three languages:

| Implementation | Language | File |
|---|---|---|
| macOS Promptly | Swift | `prompt-shields-macos-widget/PromptShields.MacOS.Widget/Managers/Accessibility/PIIDetector.swift` |
| Chrome / Edge extension | JS | `prompt-shields-chrome-extension/lib/pii-detector.js` |
| Safari extension | JS | `prompt-shields-safari-widget/PromptShieldsExtension/lib/pii-detector.js` |
| atlas backend evaluator (subset) | Python | `atlas.ai/backend/app/agents/policy_evaluator.py` |

Without a shared fixture they drift silently. A user gets redacted on macOS, types the same prompt on Chrome at home, gets through. Worse: their `PolicyViolation` events to atlas have inconsistent categories and the dashboard's *"412 redacted last 30d"* number stops being meaningful.

This file is the test that catches the drift.

## How each implementation runs it

### Swift (macOS Promptly)

Vendored as a Bundle resource at `PromptShieldsTests/Fixtures/pii-fixtures.json`. `PIIDetectorTests.swift` adds a `testSharedFixture()` method that:

1. Loads the JSON via `Bundle.module.url(forResource: "pii-fixtures", withExtension: "json")`
2. Decodes into a `Fixture` struct mirroring the schema below
3. For each case, runs `PIIDetector.findMatches(in: text)`
4. Asserts category membership against `expect` / `expectIncludes` / `expectExcludes`

### JS (Chrome + Safari)

Vendored as `lib/pii-fixtures.json` in each extension. The runner script lives at `tests/run-fixture.js` (committed in each extension repo) and is invoked via `npm test` or directly with `node tests/run-fixture.js`. Pseudocode:

```
load lib/pii-detector.js into a Node global scope
load lib/pii-fixtures.json
for each case:
  text = case.text or generated from textRepeat
  hits = D.findMatches(text)
  cats = hits.map(h => h.category)
  pass if:
    case.expect == [] AND hits.length == 0
    OR case.expectIncludes is a subset of cats
    AND case.expectExcludes is disjoint from cats
exit 1 on any failure
```

The JS extensions each commit a small `tests/run-fixture.js` driver. See `prompt-shields-chrome-extension/tests/run-fixture.js` for the reference implementation.

### Python (atlas backend)

The backend's `policy_evaluator.py` doesn't expose a stand-alone PII detector — its detectors are parameterised via policy templates. To exercise the same fixture in Python, atlas-side tests would need a thin wrapper that runs the canonical category set.

Today the Python tests in `backend/tests/unit/test_policy_evaluator.py` cover the dispatch + per-detector primitives independently. Adding a `test_shared_fixture` is a follow-up — it requires a small `pii_detector.py` module that mirrors the canonical 11-category rule set with Luhn / phone / bigram validators (the macOS detector's exact shape).

## Case schema

```jsonc
{
  "id": "credit-card-luhn-invalid",
  "text": "tracking 4532-1488-0343-6468 from carrier",
  "expectExcludes": ["creditCard"],
  "expectIncludes": ["phone"],
  "notes": "Same SHAPE as a credit card but Luhn fails — must fall through to phone."
}
```

| Field | Type | Required | Meaning |
|---|---|---|---|
| `id` | string | yes | Unique identifier (kebab-case) |
| `text` | string | one of `text` or `textRepeat` | Input text to scan |
| `textRepeat` | `{pattern, count}` | one of `text` or `textRepeat` | Repeat pattern N times — for length-cap tests |
| `expect` | string[] | optional | EXACT category set the implementation must produce. Use `[]` for "no detections". |
| `expectIncludes` | string[] | optional | Category set must INCLUDE all of these |
| `expectExcludes` | string[] | optional | Category set must EXCLUDE all of these |
| `notes` | string | yes | Human-readable explanation — what this case validates |

Most cases use `expectIncludes` + `expectExcludes` rather than `expect` so per-implementation differences in the order categories are reported don't break tests. Only category *membership* matters.

## Adding a case

1. Add an entry to `cases[]` with a fresh `id`.
2. Bump `version` (semver — patch for new cases, minor for schema changes).
3. Run the fixture against ALL three implementations locally.
4. If any fails, the contract drifted — fix the implementations or update the fixture if the new behaviour is intended.
5. Coordinate the release: `pii-fixtures.json` lands in atlas first, then the three client repos vendor the new copy.

## Coverage today

| Category | Cases |
|---|---|
| email | 2 (canonical + plus-tag) |
| phone | 3 (US format + too-short + too-long) |
| creditCard | 3 (Visa-valid + Luhn-invalid fall-through + Amex 15-digit) |
| ssn | 2 (canonical + non-dashed reject) |
| ipAddress | 2 (canonical + out-of-range octets) |
| apiKey | 3 (AWS + OpenAI + Stripe) |
| jwt | 1 |
| iban | 1 |
| bitcoinAddress | 2 (Bech32 + P2PKH) |
| currency | 3 (EUR-M + USD-thousand + NOK ISO) |
| personName | 2 (mid-sentence + sentence-start) |
| Bigram suppression | 2 (United States + Machine Learning) |
| Multi-detection / overlap | 2 |
| Scan-length cap | 1 |
| Empty / clean | 2 |

**31 cases.** Adding cases is preferred to deleting — the corpus is append-mostly.
