"""Database engine, session factory, and declarative base.

Uses SQLAlchemy 2.0 async support with the `asyncpg` driver.
PostgreSQL is the source of truth for all persistent data.
"""

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Declarative base for all ORM models.

    `eager_defaults=True` ensures server-side defaults (and `onupdate`
    columns such as `updated_at`) are fetched during the flush transaction
    instead of via a lazy load, which is essential for async sessions where
    lazy attribute access outside the greenlet context is not possible.
    """

    __mapper_args__ = {"eager_defaults": True}


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a database session.

    Commits on success and rolls back on failure. The session is always
    closed, but `expire_on_commit=False` keeps attributes usable by the
    response serializers.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
