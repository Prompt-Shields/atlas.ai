# AI-SPM → atlas.ai merge design (tenancy + auth)

**Date:** 2026-05-05  
**Goal:** Merge the AI-SPM dashboard + policy enforcement domain into atlas.ai while preserving atlas.ai login flow and guaranteeing strict tenant isolation.

---

## Scope

- Merge milestones 0–6 from `docs/ai-spm-merge-work-order.md` (domain models → API → frontend pages → worker cron → rebrand).
- Preserve atlas.ai authentication UX and `/dashboard/*` route-guard behavior.
- Ensure the AI-SPM API surface matches existing client wire formats, especially for policy enforcement endpoints.

> **Authority:** this spec overrides the work order on §Tenancy and §Auth. Where the work order's task instructions and this spec disagree on those topics, follow the spec.

---

## Current atlas.ai constraints (observed)

- JWTs include `tenant_id` + `org_id` and roles; backend code commonly applies explicit tenant filters in routers.
- RLS policies exist in migrations and depend on `current_setting('app.current_tenant_id', TRUE)`.
- The application does not currently set `app.current_tenant_id` per request DB session, so RLS is not enforced unless we add wiring.

---

## Tenancy isolation design (defense in depth)

### Invariants

1. **Every persisted AI-SPM row is tenant-stamped**
   - Any org-scoped AI-SPM model includes `tenant_id` + `org_id`.
   - Writes derive these values from authenticated user context (JWT).

2. **Cross-tenant access is blocked twice**
   - **App-level filters** (explicit `WHERE model.tenant_id == user.tenant_id` unless super-admin)
   - **DB RLS** enforced via `SET LOCAL app.current_tenant_id = '<tenant_uuid>'` on the request’s DB session

3. **Super-admin behavior is explicit**
   - By default, super-admin requests do **not** set `app.current_tenant_id`.
   - Any endpoint that must operate within a tenant must do so explicitly (tenant_id parameter + `SET LOCAL`, and/or explicit tenant filters).

### Implementation note

The request-scoped DB session used by each handler must have `app.current_tenant_id` set *before* any tenant-scoped SELECT/INSERT/UPDATE is performed. This is required for existing and new RLS policies to work as intended.

To avoid “set on one session, query on another” bugs, tenant-scoped routers should use a DB-session dependency that depends on `AuthUser` and executes `SET LOCAL` on the session it yields.

---

## Auth design for AI-SPM endpoints

### Backend API (human/admin UI)

- All `/api/v1/*` endpoints used by the dashboard UI are JWT-authenticated the same way existing atlas.ai routes are.

### PEP ingestion: policy violations

- `/api/v1/policies/violations` is **JWT-only** (Bearer token).
- Tenant context is derived from the JWT claims `tenant_id`.
- Reject the request if the JWT carries no `tenant_id`.
- The request handler must:
  - Validate JWT and derive tenant context (above)
  - Enforce prompt-hash constraint (hex64) and reject payloads that imply raw prompt leakage (defense in depth)
  - Store violations tenant-scoped and query them tenant-scoped (explicit `WHERE tenant_id = ...` AND `SET LOCAL app.current_tenant_id` per Milestone 0)

---

## Wire-format compatibility

- Policy enforcement wire formats (templates, instances, violations) must match the AI-SPM dashboard types, especially `PolicyInstance` camelCase keys.
- Backend Pydantic schemas should use aliases (`Field(alias="...")` + `populate_by_name=True`) so Python remains snake_case while JSON matches clients.

Add at least one backend contract test (`backend/tests/contract/test_policy_wire_format.py`) that serializes `PolicyInstance` and asserts the camelCase key set matches the expected TS contract (no snake_case leakage).

The camelCase key set asserted by the contract test must align with the JSON literal pinned in `prompt-shields-macos-widget`'s `PolicyTypesTests`. When either side changes, both must change in the same release — otherwise the macOS Promptly client silently drifts off the wire format.

---

## Verification strategy

- Add a dedicated tenancy test proving RLS is actually enforced (before adding more RLS-enabled tables).
- For each milestone:
  - Backend: `pytest && ruff check && mypy app`
  - Frontend: `npm run type-check && npm run lint`
  - Contract tests for policy endpoints where practical (snapshot JSON shape).

