"""Health and readiness endpoints.

- GET /health  -> liveness: the application process is up and responding.
- GET /ready   -> readiness: required dependencies (DB, etc.) are reachable.
"""

from datetime import datetime, timezone

from fastapi import APIRouter

from app.config import settings
from app.core.dependencies import check_dependencies

router = APIRouter(tags=["health"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@router.get("/health")
async def health():
    """Liveness probe - the service is running."""
    return {
        "status": "ok",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "timestamp": _now(),
    }


@router.get("/ready")
async def ready():
    """Readiness probe - required dependencies are reachable."""
    deps = await check_dependencies()
    ready = all(v.get("ok", False) for v in deps.values())
    return {
        "status": "ready" if ready else "not_ready",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "dependencies": deps,
        "timestamp": _now(),
    }
