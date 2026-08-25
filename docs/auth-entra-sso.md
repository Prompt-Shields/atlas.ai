# Authentication — Microsoft Entra ID SSO & self-serve sign-up

Atlas supports three ways to get an account:

1. **Password sign-up** — `POST /api/v1/signup` creates a tenant + `TENANT_ADMIN`
   with a local password and a 14-day trial (`signup_service.signup`).
2. **Invite** — an admin invites users into an existing tenant.
3. **Microsoft Entra ID SSO** — "Sign in with Microsoft" / "Continue with
   Microsoft", covered below.

## SSO flow (PRO-55 + SP-Azure)

Entry point: `GET /api/v1/auth/sso/microsoft/login` → Microsoft authorize →
`GET /api/v1/auth/sso/microsoft/callback`. The callback exchanges the code over
the back-channel, validates the id-token claims (issuer, audience, nonce,
expiry), reads `oid` (Entra object id), `email`, and `tid` (Azure directory id),
then hands off to the SPA via a one-time code (no tokens in the URL). See
`app/services/sso_login.py`.

Trust model: the id-token is obtained over TLS from Microsoft, so claims are
validated but the RS256 signature is not (yet) re-verified against JWKS — a
documented hardening follow-up.

### Provision policy — two modes

Controlled by the `SSO_SELF_SERVE_SIGNUP` flag (default **false**):

| Situation | `SSO_SELF_SERVE_SIGNUP=false` (login-only) | `SSO_SELF_SERVE_SIGNUP=true` (self-serve) |
|---|---|---|
| Identity already linked (matched by `oid`) | Sign in | Sign in |
| Existing platform user, same email, no `oid` yet | Link (backfill `oid`), sign in | Link (backfill `oid`), sign in |
| Unknown identity, **first** user of the Azure `tid` | **Rejected** (`not_provisioned`) | **JIT**: create tenant (`azure_tenant_id = tid`) + this user as `TENANT_ADMIN`, start a 14-day trial |
| Unknown identity, Azure `tid` already maps to a tenant | **Rejected** | Join that tenant as a member (`ANALYST`) |

Because email matching runs before JIT, an existing email always **links** to
the current account rather than creating a duplicate or a second tenant.

Key rules:
- One Azure directory (`tid`) maps to at most one Atlas tenant
  (`Tenant.azure_tenant_id` is unique) — colleagues auto-join the same workspace.
- SSO users have no local password (`users.hashed_password` is nullable);
  their email is treated as verified (Microsoft asserts it).
- The trial is app-managed / no-card (`subscription_status=TRIALING`,
  `trial_ends_at = now + 14d`); Stripe conversion is a separate epic.

## Configuration

See `.env.example`:

- `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_SIGNING_SECRET`
  — multi-tenant Entra app (`tenant=common`); reused from the integrations flow.
- `MICROSOFT_SSO_REDIRECT_URL` — must match the app registration redirect URI.
- `FRONTEND_BASE_URL` — where the callback sends the browser for the token handoff.
- `SSO_SELF_SERVE_SIGNUP` — `false` (login-only) or `true` (JIT self-serve).

## Code map

- `app/services/sso_login.py` — claims validation, link-only resolution,
  `complete_sso_login` (self-serve branch).
- `app/services/signup_service.py` — `provision_tenant()` (shared by password
  signup and SSO), `provision_or_join_sso_user()` (JIT).
- `app/routers/auth.py` — `/sso/microsoft/login`, `/sso/microsoft/callback`,
  `/sso/exchange`.
- Migration `027_tenant_azure_tenant_id` — adds `Tenant.azure_tenant_id`.
- Frontend — `/login` and `/signup` pages both offer the Microsoft button.
