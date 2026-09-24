# =============================================================================
# AI Real Estate Cold Calling Agent - Production image (Phase 17)
#
# Stage 1 (builder): resolve the full dependency tree from uv.lock and install
#                    the app package into a virtualenv.
# Stage 2 (runtime): slim Python with just the installed app + venv, running
#                    as a non-root user for security.
#
# The app installs from pyproject.toml (NOT requirements.txt, which contains a
# machine-specific editable path that would not resolve in Linux).
# =============================================================================
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS builder

# Keeps Python from generating .pyc files in the container.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies first (better layer caching).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Now copy the project source and install the package itself.
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts ./scripts
COPY .env.example .env.example
COPY README.md ./
RUN uv sync --frozen --no-dev

# -----------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PORT=8000

WORKDIR /app

# Copy the virtualenv produced by the builder (includes the app package).
COPY --from=builder /app/.venv /app/.venv
# App sources needed at runtime (migrations + entry code).
COPY --from=builder /app/app /app/app
COPY --from=builder /app/alembic /app/alembic
COPY --from=builder /app/alembic.ini /app/alembic.ini
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

# Non-root user for least-privilege operation (Phase 16 hardening).
RUN adduser --disabled-password --gecos "" appuser \
    && chown -R appuser:appuser /app \
    && chmod +x /usr/local/bin/entrypoint.sh

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys,os; u='http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health'; sys.exit(0 if urllib.request.urlopen(u, timeout=3).status==200 else 1)" || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
