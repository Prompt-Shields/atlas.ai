# AI-GRC Platform (Atlas.ai)

## 1. Project Name

**AI-GRC Platform** (repository: `atlas.ai`, internal name: `aigrc`)

## 2. Overview

AI-GRC is an AI-powered Governance, Risk, and Compliance (GRC) platform. It ingests text data from multiple sources (Microsoft Defender, Purview, manual input), uses Azure OpenAI (GPT-4) via Semantic Kernel to analyze risks, generate mitigations, correlate findings across records, and produce actionable compliance plans. The platform is multi-tenant with role-based access control and real-time event streaming.

## 3. Tech Stack

**Backend:**
- Python 3.12, FastAPI, Uvicorn (with uvloop)
- SQLAlchemy 2.0 (async) + asyncpg
- Alembic (migrations)
- PostgreSQL 16 with pgvector extension (vector similarity search)
- Redis 7 (caching, rate limiting, token store)
- Semantic Kernel 1.39 (Azure OpenAI orchestration)
- Pydantic v2 + pydantic-settings
- Argon2 (password hashing), python-jose (JWT)
- SSE-Starlette (server-sent events)
- OpenTelemetry + Azure Monitor (observability)
- structlog (structured logging)

**Frontend:**
- Next.js 16, React 19, TypeScript
- Tailwind CSS 4
- Heroicons

**Worker:**
- Python 3.12, shares backend app code
- Modes: dispatcher (continuous), risk_analyzer (cron job), correlation_engine (cron job), device_risk_engine (cron job)

**Infrastructure:**
- Docker Compose (local dev)
- Azure Bicep (IaC): Container Apps, Cosmos DB for PostgreSQL, Key Vault, ACR, APIM, Application Insights
- GitHub Actions (CI/CD)

## 4. Architecture

### High-Level Flow

```
Data Sources (Defender, Purview, Manual)
    |
    v
Input Adapters --> Blob Records (pgvector)
    |
    v
Risk Analyzer Agent (Azure OpenAI) --> Risk Mitigations
    |
    v
Correlation Engine Agent (Azure OpenAI) --> Correlation Action Plans
    |
    v
Dispatch Events --> SSE Stream --> Frontend Dashboard
```

### Folder Structure

```
atlas.ai/
  backend/
    app/
      adapters/         # Data source adapters (manual, purview, defender)
      agents/           # LLM-powered agents (risk_analyzer, correlation_engine, prompts)
      auth/             # JWT, API keys, rate limiting, password hashing, token store
      middleware/        # Audit logging, correlation ID, tenant context
      models/           # SQLAlchemy ORM models (tenant, user, blob, risk, correlation, dispatch, etc.)
      routers/          # FastAPI route handlers
      schemas/          # Pydantic request/response schemas
      services/         # Business logic (auth, audit, dispatch, email, invite, tenant)
      config.py         # Settings via pydantic-settings
      database.py       # Async SQLAlchemy engine/session
      errors.py         # Custom exception handlers
      main.py           # FastAPI app factory
      observability.py  # OpenTelemetry + Azure Monitor setup
    alembic/            # Database migrations
    scripts/            # DB init scripts
  frontend/
    src/
      app/
        dashboard/      # Dashboard pages (risks, correlations, blobs, feed, tenants, users, admin)
        login/          # Login page
        invite/         # Invite acceptance page
      lib/              # API client, types
  worker/
    app/
      main.py           # Worker entry point (dispatcher, risk_analyzer, correlation_engine)
  infra/
    main.bicep          # Azure infrastructure-as-code
    modules/            # Bicep modules (container-apps, cosmos-postgres, key-vault, monitoring, apim)
  .github/workflows/    # CI (lint, test), CD (dev, prod)
```

### Multi-Tenancy

The platform uses a multi-tenant architecture with PostgreSQL Row-Level Security (RLS) policies on tenant-scoped tables. Data isolation is enforced at the database level via `app.current_tenant_id` session variable. Database schemas are split into `grc` (domain data) and `audit` (audit logs).

### Roles

SUPER_ADMIN, TENANT_ADMIN, ORG_ADMIN, ANALYST, VIEWER

## 5. Key Features

- **Data Ingestion**: Pluggable adapter system for ingesting text blobs from Microsoft Defender, Microsoft Purview, and manual input. Adapters are registered via a registry pattern.
- **AI Risk Analysis**: Azure OpenAI-powered agent analyzes ingested blobs, extracts risks with severity/likelihood scores, generates mitigations with citations back to source data.
- **Risk Correlation**: Second AI agent correlates multiple risk/mitigation records to find patterns, dependencies, and compound risks, producing prioritized action plans.
- **Real-Time Event Feed**: Server-Sent Events (SSE) stream dispatch events to the frontend dashboard for live updates.
- **Multi-Tenant Isolation**: Row-level security, tenant-scoped queries, and middleware-enforced tenant context.
- **Auth & Security**: JWT access/refresh tokens, API key authentication (two-factor for sensitive endpoints), Argon2 password hashing, Redis-backed rate limiting and token revocation.
- **Invite System**: Token-based email invites for onboarding new tenant users.
- **LLM Usage Monitoring**: Tracks token consumption, estimated costs, and model usage per tenant.
- **Audit Logging**: Middleware-based audit trail for all API operations.
- **Observability**: OpenTelemetry instrumentation with Azure Application Insights export.
- **Outbox Pattern**: Reliable event delivery via an outbox table with retry logic, processed by the dispatcher worker.

## 6. Setup & Configuration

### Prerequisites

- Docker and Docker Compose
- Node.js (for frontend development outside Docker)
- Python 3.12 (for backend development outside Docker)

### Local Development

1. Copy environment file:
   ```bash
   cp .env.example .env
   ```

2. Start all services:
   ```bash
   docker compose up
   ```

   This starts: PostgreSQL 16 (pgvector), Redis 7, MailHog (email testing), Backend API (port 8000), Worker (dispatcher), and Frontend (port 3000).

3. Access the application:
   - Frontend: http://localhost:3000
   - Backend API docs: http://localhost:8000/docs
   - MailHog UI: http://localhost:8025

4. Run risk/correlation jobs on demand:
   ```bash
   docker compose --profile jobs run risk-job
   docker compose --profile jobs run correlation-job
   ```

### Key Environment Variables

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string (async) |
| `REDIS_URL` | Redis connection string |
| `JWT_SECRET_KEY` | JWT signing secret (min 32 chars in prod) |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI service endpoint |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | Model deployment name (default: gpt-4o) |
| `SUPER_ADMIN_EMAIL` / `SUPER_ADMIN_PASSWORD` | Bootstrap admin credentials |
| `WORKER_MODE` | Worker mode: `dispatcher`, `risk_analyzer`, `correlation_engine`, or `device_risk_engine` |

## 7. Dependencies

### Backend (Key)

| Package | Purpose |
|---|---|
| `fastapi` | Web framework |
| `sqlalchemy` + `asyncpg` | Async ORM and PostgreSQL driver |
| `alembic` | Database migrations |
| `pgvector` | Vector similarity search in PostgreSQL |
| `semantic-kernel` | Azure OpenAI agent orchestration |
| `python-jose` | JWT token handling |
| `argon2-cffi` | Password hashing |
| `redis` | Caching, rate limiting, token store |
| `sse-starlette` | Server-Sent Events |
| `opentelemetry-*` | Distributed tracing and metrics |
| `azure-monitor-opentelemetry-exporter` | Azure Application Insights export |
| `structlog` | Structured logging |
| `tenacity` | Retry logic |

### Frontend (Key)

| Package | Purpose |
|---|---|
| `next` (16.x) | React framework |
| `react` (19.x) | UI library |
| `tailwindcss` (4.x) | Utility-first CSS |
| `@heroicons/react` | Icon library |

### Infrastructure

| Service | Purpose |
|---|---|
| PostgreSQL 16 + pgvector | Primary database with vector search |
| Redis 7 | Caching, rate limiting, token blacklist |
| Azure Container Apps | Production container hosting |
| Azure Cosmos DB for PostgreSQL | Production database |
| Azure Key Vault | Secrets management |
| Azure APIM | API gateway |
| Azure Application Insights | Monitoring and telemetry |

## 8. API Endpoints

All API routes are prefixed with `/api/v1`.

### Auth (`/api/v1/auth`)
- `POST /login` - Authenticate and receive JWT token pair
- `POST /refresh` - Refresh access token
- `POST /logout` - Revoke tokens
- `POST /change-password` - Change user password
- `GET /api-keys` - List user's API keys
- `POST /api-keys` - Create a new API key

### Tenants (`/api/v1/tenants`)
- CRUD operations for tenant management

### Users (`/api/v1/users`)
- User management within tenant scope

### Invites (`/api/v1/invites`)
- Token-based invite creation and acceptance

### Adapters (`/api/v1/adapters`)
- `GET /available` - List registered input adapters
- `POST /manual/ingest` - Ingest text blobs (requires JWT + API key)
- `GET /blobs` - List ingested blob records

### Risks (`/api/v1/risks`)
- `GET /` - List risk/mitigation records (tenant-scoped, paginated)

### Correlations (`/api/v1/correlations`)
- `GET /` - List correlation/action plan records (tenant-scoped, paginated)

### Dispatch (`/api/v1/dispatch`)
- `GET /events` - List dispatch events
- `GET /stream` - SSE stream for real-time events

### Monitoring (`/api/v1/monitoring`)
- `GET /llm-usage` - LLM token usage and cost tracking

### Admin (`/api/v1/admin`)
- Super admin operations

### Health
- `GET /health` - Application health check (non-versioned)
