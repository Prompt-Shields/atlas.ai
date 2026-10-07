# Promptly Guide adoption figures

Promptly Guide is an employee-facing Mac app that guides people through their work apps and AI tools. Its central promise (requirement **E4** in the promptly-guide repository) is that **the employer learns nothing about one person**. Guide's issues #37 and #38 cover how its signals reach Atlas, and this page describes the Atlas side of that.

- Code: `app/routers/guide.py`, `app/services/guide_adoption_service.py`, `app/models/guide_adoption.py`, `app/auth/firebase_token.py`.
- Migration: `046`.

## The decisions this is built on

- **Nothing by default** (promptly-guide #38). No adoption signal leaves a Mac unless the person using it opted in. That holds for every kind of figure: topics, apps and walkthrough completions.
- **The organisation offers; the person decides.** A tenant chooses which kinds it wants counted (`offered_kinds`). Guide shows that offer to the person, and the person says yes or no.
- **No device row, no person row.** Guide Macs are not enrolled through `devices/register`. That endpoint stores `user_external_id` on every device, which is the per-person row E4 rules out.

## How a Guide Mac authenticates

Guide signs people in through the organisation's identity provider, by way of a Google Firebase Authentication (Identity Platform) project. It sends that Firebase **ID token** as the Bearer token on each call. Atlas then works through these steps:

1. It reads the token's `aud` (the Firebase project) and `firebase.tenant` without trusting them. It uses them to find the tenant's `GuideConnection` through `grc.resolve_guide_connection`, a SECURITY DEFINER resolver built the same way as `resolve_device_by_token`.
2. It verifies the token against that project, as Firebase documents:
   - RS256, signed by one of Google's `securetoken` certificates (cached for the `max-age` Google sends);
   - the `iss` and `aud` are right;
   - not expired, not issued in the future;
   - has a sign-in time and a non-empty `sub`.
3. It sets the tenant GUC. From here on, RLS applies.

From the token, Atlas uses only the project, the tenant and `sub`. It never reads, logs or stores the email, name or groups. An unknown project gets the same `401` as a bad token, so nobody can find out which projects are connected by asking.

## What is stored

Nothing is stored per person. A contribution is folded into running counts as it arrives:

| Table | Holds |
| --- | --- |
| `guide_connections` | The tenant's Firebase project, its Identity Platform tenant, and `offered_kinds` |
| `guide_adoption_contributors` | Tenant, month, team, and how many people contributed |
| `guide_adoption_counts` | Tenant, month, team, category, and how many of those people reached it |
| `guide_adoption_receipts` | Tenant, month, and a keyed hash of (tenant, `sub`, month), so that one person counts once a month |

The three adoption tables have **no timestamp columns**. A receipt and a count updated in the same second could otherwise be lined up, which would tie a person's receipt to their team and categories.

The receipt is an HMAC keyed by a value derived from the server secret, so nobody can recompute it from a list of emails. No API reads it. Rotating `JWT_SECRET_KEY` resets the once-a-month check for the current month. That is acceptable, because a second contribution from the same person can only add one to counts that the gate bands anyway.

## What a contribution may say

```json
POST /api/v1/guide/adoption
{ "team": "sales", "period": { "year": 2026, "month": 9 },
  "reached": [ { "kind": "app", "id": "Claude" }, { "kind": "completion", "id": "finished" } ] }
```

- **Only a finished month**, from the last three. Guide sends after a month ends. Accepting a month still running would let one more contribution move a band while someone watches.
- **Only compiled-in identifiers.** Each kind has its own pattern:
  - topic: `<pack>/<entry>`;
  - app: a well-known tool's name;
  - completion: a walkthrough ending.

  Free text is refused, and so is any field beyond these three (`extra="forbid"`).
- **Only offered kinds.** A category of a kind the tenant does not offer rejects the whole contribution. Guide only sends what was offered, so a client that sends anything else is suspect.
- A second contribution from the same person for the same month returns `{"counted": false}` and changes nothing.

## What an admin sees

`GET /api/v1/guide/adoption/report?period=2026-09` (Analyst and above) runs the counts through the gate. The gate is a port of Guide's `AdoptionGate`, and the two must keep the same rules:

- A team with **fewer than 10** contributors that month reports **nothing**, not even which categories it touched. Its name is listed under `teams_too_small`.
- Within a team that reports, a category that reached fewer than 10 people is **suppressed, not rounded**. The report says how many were left out per team, but never which ones.
- Figures are **bands** (`under 20%`, `20–29%` … `80% or more`), and there is **no team total**. No figure can be recovered by subtraction, and none can say "all of the team" or "none of them".
- Only kinds the tenant offers right now are shown.

Every report carries a note: only people who chose to be counted are in these figures, so a band is a share of them, not of the whole team.

## Asking for more

A figures request that names a person, a user, an email or a device is refused with `400 ADOPTION_IS_BY_TEAM`, and the response says why. It is not answered with an empty report, which would read as "nobody". There is no per-person data to answer it with anyway. promptly-guide's `docs/adoption-analytics.md` ("When a customer asks for more") states the whole rule. The minimum group size of 10 is pinned in tests here and in Guide.

## The 30-day pilot report (promptly-guide #39)

`GET /api/v1/guide/pilot-report` (Analyst and above). Add `?format=markdown` for a shareable copy. This is the end-of-pilot report, and the Atlas demo. It is built from aggregate data only, and it is not available until 30 days after Guide was connected: before that the endpoint returns `409 PILOT_NOT_READY` with the date it will be ready.

| Section | From | Through |
| --- | --- | --- |
| AI tools in use | Guide's `app` figures | The gate above: teams of 10 or more, bands, no totals |
| Where people get stuck | Guide's `completion` figures (walkthroughs finished, and where they stopped) and `topic` figures (what people needed help with) | The same gate |
| Risky behaviour | Atlas's own prompt telemetry from the Prompt Shields clients (`grc.prompt_events`): violations by kind of personal data, by AI tool, and by what was done about them | Counts for the whole tenant. **A row is shown only if at least 10 devices contributed to it**; the report says how many rows were left out, but not which |

- **Months covered:** Guide's figures are monthly, so the report covers the finished months since connection, at most three.
- **Risk window:** the risk section covers the same days.
- **Prompt tips:** Guide's prompt tips are not counted (promptly-guide #34), so nothing in the risk section comes from Guide.

## Approved tools for steering (promptly-guide #57)

`GET /api/v1/guide/approved-tools` is authenticated by the Guide caller's Firebase ID token, the same way as the adoption calls. It returns the organisation's sanctioned AI tools, taken from its AI use-case registry:
- every tool that an **ACTIVE** use case names, with names compared without case;
- for each tool, the union of what its use cases are approved for, translated into Guide's data classes (`customer_pii` becomes "customer data", `proprietary_code` becomes "source code", and so on);
- taxonomy values with no Guide counterpart are left out.

The response holds tool names and data classes only, never who registered or owns a use case. Guide uses it to steer someone in an unapproved AI tool towards an approved one, alongside the organisation's own `approved-tools.json`.

A use case scoped to one department still counts as approval for the whole organisation here. Guide has no department to match it against.

## SCIM provisioning, for group-based enablement (promptly-guide #58)

Atlas hosts a SCIM 2.0 endpoint at `/api/v1/scim/v2`. The customer's Entra ID provisioning service pushes users and groups into it.

- **Token.** A tenant admin makes the token with `POST /api/v1/guide/scim-token`. The response shows it once, and only a hash is stored. Making a new token replaces the old one, and `DELETE /api/v1/guide/scim-token` revokes it. Paste the token and the tenant URL into Entra's provisioning settings.
- **Supported calls.** This is the subset Entra's provisioning uses:
  - Users and Groups: list, get, create, delete;
  - `eq` filters on `userName`, `externalId` and `displayName`;
  - PUT for Users;
  - PATCH: `active`, `userName` and `externalId` on Users; add, remove and replace `members`, and `displayName`, on Groups;
  - `ServiceProviderConfig`.

  Errors come back in SCIM's own error shape.
- **What is stored** (`app/models/guide_scim.py`):
  - a user is a `userName`, an `externalId` and `active`;
  - a group is a `displayName`, an `externalId` and its members.

  Entra also sends names, emails, titles, managers and phone numbers. Atlas accepts them and stores none of them.
- **What Guide reads.** `GET /api/v1/guide/groups` uses the Guide caller's Firebase token. Atlas reads the token's email for this endpoint only, matches it, case-insensitively, against a provisioned, active `userName`, and returns that person's group names. Someone deprovisioned (`active` false) or never provisioned gets no groups. Nothing about what Guide does is joined to these tables.
- **Kept apart from the Graph pull.** These are separate tables from `directory_*`: the push is its own, thinner store, under its own token.

## Setting it up

`PUT /api/v1/guide/connection` (TenantAdmin) with `firebase_project_id`, optionally `firebase_tenant_id`, and `offered_kinds`. A Firebase project and tenant can belong to only one Atlas tenant (`409` otherwise). An empty `offered_kinds` means the tenant counts nothing, and Guide does not ask anyone.

## Limits

- **Team is client-sent.** Guide takes it from the organisation's configuration profile, so a person on a Mac they administer could name another team. The minimum group size and bands blunt the effect, but they do not prevent it.
- **The request itself reveals the caller to Atlas for its duration.** The token is verified and then dropped. The audit middleware does not cover `/api/v1/guide/`, so no client IP is logged against a contribution. Keep it that way, and keep request logs free of `Authorization` headers.
- **Differencing across months** can narrow a band for a team that changed size. The same limit is documented in Guide's `docs/adoption-analytics.md`.
