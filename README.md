# atlas.ai

**An AI governance (AI-GRC) platform.** Atlas inventories the AI systems in use
across an organisation, maps them to risks, owners, and controls, and enforces
policy on the endpoints where AI is actually used.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

---

## What's in this repository

| Path | What it is |
|---|---|
| `backend/` | FastAPI + SQLAlchemy + Alembic API. Postgres in dev/prod, SQLite for unit tests. |
| `frontend/` | Next.js 15 (App Router) dashboard, served under `/dashboard/*`. |
| `worker/` | Background job runner — risk extraction, correlation, device-risk scoring. |
| `infra/` | Azure Bicep templates and APIM policies. |
| `tests/` | Cross-cutting integration and detector fixture tests. |
| `docs/` | Architecture, integration, and admin documentation. |

**Multi-tenancy is enforced in depth:** application-level tenant scoping *plus*
Postgres Row Level Security. See [Tenancy rules](#tenancy-rules-read-before-touching-the-data-layer)
before writing any query.

---

## Quickstart

### Prerequisites

- **Docker** and **Docker Compose**
- **Python** — see `backend/pyproject.toml` for the supported version
- **Node.js 20+** for the frontend

### 1. Configure

```bash
cp .env.example .env
```

Open `.env` and replace every `CHANGE_ME` value. For local development the
defaults for Postgres, Redis, and MailHog match the Compose file, so the only
values you must set are `JWT_SECRET_KEY` and `MICROSOFT_SIGNING_SECRET`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Azure OpenAI values (`AZURE_OPENAI_*`) are only needed for the AI analysis
features; the rest of the app runs without them.

> `.env` is gitignored and must stay that way. Never commit real credentials —
> see [SECURITY.md](SECURITY.md).

### 2. Start the stack

```bash
docker compose up -d
```

This brings up Postgres (with pgvector), Redis, MailHog, the backend, the
worker, and the frontend.

| Service | URL |
|---|---|
| Frontend | http://localhost:3000 |
| Backend API | http://localhost:8000 |
| API docs (OpenAPI) | http://localhost:8000/docs |
| MailHog (captured email) | http://localhost:8025 |

### 3. Run the backend outside Docker (for iteration)

```bash
cd backend
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

---

## Development

### Backend

```bash
cd backend
pytest -m "not e2e"      # unit + integration tests
ruff check               # lint
ruff format              # format
mypy app                 # type check
```

Database migrations use Alembic:

```bash
alembic upgrade head                          # apply
alembic revision --autogenerate -m "message"  # create
```

### Frontend

```bash
cd frontend
npm install
npm run dev          # http://localhost:3000
npm run lint
npm run type-check
npm run build
```

### Pre-commit hooks

```bash
pre-commit install
```

---

## Tenancy rules (read before touching the data layer)

Cross-tenant data leakage is the highest-severity class of bug in this codebase.
Two independent mechanisms must both hold:

1. **Every tenant-scoped row stores `tenant_id` *and* `org_id`.** No exceptions.
2. **Application-level filtering** must prevent cross-tenant reads and writes —
   never rely on RLS alone as the only guard.
3. **Postgres RLS** policies key off:
   ```sql
   current_setting('app.current_tenant_id', true)::uuid
   ```
4. **Any request handler touching a tenant-scoped table** must ensure the session
   sets the tenant before querying:
   ```sql
   SET LOCAL app.current_tenant_id = '<tenant_uuid>';
   ```

A pull request that adds a tenant-scoped table without both `tenant_id`/`org_id`
columns and a matching RLS policy will not be merged.

---

## Documentation

| Doc | Covers |
|---|---|
| [`docs/auth-entra-sso.md`](docs/auth-entra-sso.md) | Microsoft Entra ID SSO and self-serve sign-up |
| [`docs/policy-enforcement-admin-guide.md`](docs/policy-enforcement-admin-guide.md) | End-to-end admin walkthrough of policy enforcement |
| [`docs/design/`](docs/design/) | Design docs — telemetry, cost ledger, AI-SPM model, device unification |
| [`docs/integrations/microsoft-sentinel/`](docs/integrations/microsoft-sentinel/) | Sentinel integration architecture and log schema |
| [`spec.md`](spec.md) | Top-level platform specification |

---

## Deployment

`.github/workflows/cd-dev.yml` and `cd-prod.yml` build images, push them to Azure
Container Registry, and deploy to Azure Container Apps. Authentication uses
OIDC federated credentials — no long-lived Azure secrets are stored.

Deployment is environment-driven. A fork must define these **repository
variables** before the workflows will run:

| Variable | Used by |
|---|---|
| `AZURE_RESOURCE_GROUP_DEV` / `_PROD` | both workflows |
| `ACR_NAME_DEV` / `_PROD` | both workflows |
| `ACR_LOGIN_SERVER_DEV` / `_PROD` | both workflows (prod also promotes *from* the dev registry) |
| `NEXT_PUBLIC_API_URL_DEV` | frontend image build |

and these **secrets**: `AZURE_CLIENT_ID_DEV`/`_PROD`, `AZURE_TENANT_ID`,
`AZURE_SUBSCRIPTION_ID_DEV`/`_PROD`.

Infrastructure is defined in `infra/main.bicep` with per-environment parameters
under `infra/environments/`.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow and conventions, and
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for community expectations.

## Security

Report vulnerabilities privately — see [SECURITY.md](SECURITY.md). Please do not
open a public issue for a security problem.

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) and
[NOTICE](NOTICE).
