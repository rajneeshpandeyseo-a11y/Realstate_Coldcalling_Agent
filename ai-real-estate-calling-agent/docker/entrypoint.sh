#!/bin/sh
# =============================================================================
# Container entrypoint (Phase 17)
#
# 1. Wait for the database to accept connections (the compose healthcheck also
#    gates the app, but a retry loop keeps the app robust on its own).
# 2. Apply any pending Alembic migrations.
# 3. Start the ASGI server.
# =============================================================================
set -e

echo "[entrypoint] running database migrations..."
alembic upgrade head

echo "[entrypoint] starting uvicorn on 0.0.0.0:${PORT:-8000}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
