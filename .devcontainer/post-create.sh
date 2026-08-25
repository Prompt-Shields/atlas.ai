#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="/workspaces/prompt-shields-atlas.ai"
cd "$WORKSPACE"

echo "==> Installing backend (editable) + dev extras..."
pip install -e './backend[dev]' --quiet

echo "==> Installing debugpy for Python debugging..."
pip install debugpy --quiet

echo "==> Creating worker_app symlink for local development..."
# In Docker the worker code lives at /app/worker_app; replicate that layout
# so 'python -m worker_app.main' works from the repo root.
ln -snf worker/app worker_app

echo "==> Installing frontend dependencies..."
(cd frontend && npm ci)

echo "==> Running database migrations..."
(cd backend && python -m alembic upgrade head) || echo "WARNING: migrations failed — is postgres ready?"

echo "==> Done! Workspace is ready."
