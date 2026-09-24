"""Core infrastructure dependencies readiness checks."""

from app.config import settings
from app.db.session import AsyncSessionLocal


async def _check_postgres() -> dict:
    """Run a lightweight `SELECT 1` against the database pool."""
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(__import__("sqlalchemy").text("SELECT 1"))
        return {"ok": True, "detail": "connected"}
    except Exception as exc:  # pragma: no cover - depends on runtime infra
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


async def check_dependencies() -> dict:
    """Return readiness status for each configured dependency.

    Each entry is `{"ok": bool, "detail": str}`.
    In test mode PostgreSQL is not required so it reports not_required.
    """
    deps: dict = {}

    if settings.ENVIRONMENT == "test":
        deps["postgresql"] = {"ok": True, "detail": "not_required"}
    else:
        deps["postgresql"] = await _check_postgres()

    return deps
