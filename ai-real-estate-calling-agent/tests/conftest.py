"""Pytest fixtures for the AI Real Estate Calling Agent.

Test environment uses a dedicated PostgreSQL database
(`real_estate_calling_test`) so tests never touch dev data.
"""

import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

# Force test environment + test database BEFORE importing app modules.
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DEBUG", "false")
os.environ.setdefault("MOCK_MODE", "true")
os.environ.setdefault("LIVE_CALLS_ENABLED", "false")
os.environ.setdefault("RECORDING_ENABLED", "false")
os.environ.setdefault("AUTO_RETRY_ENABLED", "false")
# `live`-marked tests only run when explicitly enabled; a plain pytest is free.
os.environ.setdefault("RUN_LIVE_TESTS", "false")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/real_estate_calling_test",
)
# Security keys used by the test client by default (Phase 16).
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key")
os.environ.setdefault("WEBHOOK_TOKEN", "test-webhook-token")

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.db.session import Base, get_db  # noqa: E402

TEST_DATABASE_URL = os.environ["DATABASE_URL"]


async def _ensure_test_database() -> None:
    """Create the dedicated test database if it does not already exist."""
    postgres_url = TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres"
    engine = create_async_engine(
        postgres_url,
        echo=False,
        pool_pre_ping=True,
        isolation_level="AUTOCOMMIT",
    )
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT 1 FROM pg_database WHERE datname = :db_name"
                ),
                {"db_name": "real_estate_calling_test"},
            )
            exists = result.scalar_one_or_none() is not None
            if not exists:
                await conn.execute(text("CREATE DATABASE real_estate_calling_test"))
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def _guard_live_tests(request):
    """Skip `live`-marked tests unless live calling is deliberately enabled.

    A plain `pytest` (deselected with `-m 'not live'`) touches nothing external.
    But if someone runs `-m live` without RUN_LIVE_TESTS=true, still refuse to
    place real (paid) calls.
    """
    if "live" in request.node.keywords and os.environ.get("RUN_LIVE_TESTS", "false").lower() != "true":
        pytest.skip("live test skipped: set RUN_LIVE_TESTS=true to run")


async def _override_get_db(session_maker):
    """Return a FastAPI dependency override bound to a given session maker."""
    async def _dep():
        async with session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()
    return _dep


@pytest_asyncio.fixture
async def db_sessionmaker():
    """Create a fresh engine + session maker, migrate, and dispose after.

    ``NullPool`` closes every connection as soon as its session ends, so no
    pooled asyncpg connection lingers across a test's event-loop teardown
    (which otherwise races the next fixture/test's loop on Windows).
    """
    await _ensure_test_database()
    engine = create_async_engine(
        TEST_DATABASE_URL, echo=False, pool_pre_ping=True, poolclass=NullPool
    )
    maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield maker
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def client(db_sessionmaker):
    """Async test client with DB dependency overridden to the test DB."""
    from app.main import app

    dep = await _override_get_db(db_sessionmaker)
    app.dependency_overrides[get_db] = dep
    transport = ASGITransport(app=app)
    headers = {
        "X-API-Key": os.environ["ADMIN_API_KEY"],
        "X-Webhook-Token": os.environ["WEBHOOK_TOKEN"],
    }
    async with AsyncClient(
        transport=transport, base_url="http://test", headers=headers
    ) as ac:
        yield ac
    app.dependency_overrides.clear()
