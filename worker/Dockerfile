# Worker service Dockerfile — shares backend code
# NOTE: Build context must be the repo root (.), not ./worker
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install backend as dependency
COPY backend/pyproject.toml /build/backend/
RUN mkdir -p /build/backend/app && touch /build/backend/app/__init__.py
RUN pip install --no-cache-dir --prefix=/install /build/backend/ || true

# Install worker deps
COPY worker/pyproject.toml ./worker/
# Fallback: install backend deps directly
RUN pip install --no-cache-dir --prefix=/install \
    fastapi uvicorn uvloop pydantic pydantic-settings sqlalchemy asyncpg alembic \
    pgvector python-jose argon2-cffi python-multipart httpx \
    itsdangerous aiosmtplib email-validator semantic-kernel \
    opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp \
    azure-monitor-opentelemetry-exporter redis sse-starlette structlog \
    tenacity croniter

FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r aigrc && useradd -r -g aigrc -s /sbin/nologin aigrc

COPY --from=builder /install /usr/local

WORKDIR /app
# Copy backend app code
COPY backend/app/ ./app/
COPY backend/alembic.ini ./
COPY backend/alembic/ ./alembic/
# Copy worker code
COPY worker/app/ ./worker_app/

RUN chown -R aigrc:aigrc /app
USER aigrc

CMD ["python", "-m", "worker_app.main"]
