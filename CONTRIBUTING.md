# Contributing to atlas.ai

Thanks for your interest in contributing.

## Before you start

- Read the [Code of Conduct](CODE_OF_CONDUCT.md).
- For security issues follow [SECURITY.md](SECURITY.md) — **never** open a public
  issue for a vulnerability.
- For anything beyond a small fix, open an issue first so we can agree on the
  approach.

## Development setup

Follow the [Quickstart](README.md#quickstart). In short:

```bash
cp .env.example .env      # then replace every CHANGE_ME value
docker compose up -d
```

Install the hooks so lint, formatting, and secret detection run before each
commit:

```bash
pre-commit install
```

The hooks include `detect-private-key` and `check-added-large-files` — leave them
enabled; they are the last line of defence before a credential reaches a public
repository.

## The one rule that matters most: tenancy

atlas.ai is multi-tenant. A cross-tenant data leak is the most serious bug this
codebase can ship, so tenancy is enforced twice — in the application and in the
database.

Every pull request touching the data layer must:

- add `tenant_id` **and** `org_id` to any new tenant-scoped table;
- add a Postgres RLS policy keyed on
  `current_setting('app.current_tenant_id', true)::uuid`;
- filter by tenant in application code as well — RLS is the backstop, not the
  only guard;
- ensure the request path issues
  `SET LOCAL app.current_tenant_id = '<tenant_uuid>'` before querying;
- include a test proving tenant A cannot read or write tenant B's rows.

Pull requests that skip any of these will be asked for changes.

## Checks that must pass

Backend:

```bash
cd backend
pytest -m "not e2e"
ruff check
ruff format --check
mypy app
```

Frontend:

```bash
cd frontend
npm run lint
npm run type-check
npm run build
```

CI runs the same checks. Please run them locally first.

## Database migrations

Schema changes go through Alembic — never hand-edit a table in a running
database:

```bash
cd backend
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```

Review the generated migration before committing; autogenerate does not always
get constraints, indexes, or RLS policies right.

## Coding conventions

- **Backend** — Python typed throughout; `mypy app` must pass. Business logic
  lives in `app/services/`, HTTP concerns in `app/routers/`, persistence in
  `app/models/`. Routers stay thin.
- **Frontend** — TypeScript, Next.js App Router, Tailwind. Server components by
  default; add `"use client"` only where interactivity requires it.
- Match the surrounding style rather than reformatting untouched code.

## Never commit

- `.env` or any file containing a real credential, endpoint, or tenant identifier
- Customer names, prospect feedback, pricing, or other commercially sensitive
  material — this is a public repository
- Local machine state, build output, or `file 2.ext` sync duplicates

Run `git status` and skim `git diff` before every commit.

## Pull requests

1. Branch from `main` with a descriptive name (`fix/rls-missing-on-assets`).
2. One concern per pull request.
3. Imperative commit subjects; explain *why* in the body.
4. Describe how you verified the change.

By contributing, you agree that your contributions are licensed under the
Apache License 2.0.
