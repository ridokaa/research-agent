"""Async SQLAlchemy engine + session factory.

Engine is initialized once at application startup (lifespan) via ``init_engine()``
and torn down via ``close_engine()``.  FastAPI endpoints receive a session through
the ``get_session`` dependency.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

import structlog
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

logger = structlog.get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


async def init_engine(
    database_url: str,
    *,
    pool_size: int = 5,
    max_overflow: int = 10,
    echo: bool = False,
) -> None:
    """Initialize the async engine and session factory.

    Args:
        database_url:  PostgreSQL async URL (must use +asyncpg driver).
        pool_size:     Number of persistent connections kept in the pool.
        max_overflow:  Extra connections allowed beyond *pool_size*.
        echo:          When ``True`` SQLAlchemy logs every SQL statement.
                       Automatically set to ``True`` when ``LOG_LEVEL=DEBUG``.
    """
    global _engine, _session_factory

    _engine = create_async_engine(
        database_url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,   # verify connection liveness before checkout
        echo=echo,
    )
    _session_factory = async_sessionmaker(
        _engine,
        expire_on_commit=False,  # avoid lazy-load after commit in async context
        autoflush=False,
    )
    logger.info(
        "db.engine_ready",
        pool_size=pool_size,
        max_overflow=max_overflow,
        echo=echo,
    )


async def close_engine() -> None:
    """Dispose the engine and all pooled connections on shutdown."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        logger.info("db.engine_closed")


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a scoped async DB session.

    Usage::

        @router.get("/example")
        async def handler(session: AsyncSession = Depends(get_session)):
            ...
    """
    if _session_factory is None:
        raise RuntimeError(
            "Database engine not initialized. Call init_engine() during app startup."
        )
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_engine() -> AsyncEngine:
    """Return the current engine (used by Alembic env.py and tests)."""
    if _engine is None:
        raise RuntimeError("Database engine not initialized.")
    return _engine
