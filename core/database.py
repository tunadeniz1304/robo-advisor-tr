"""Asynchronous database engine, session factory and declarative base.

This module owns every SQLAlchemy infrastructure concern:

    * :class:`Base` — the declarative base every ORM model inherits from.
    * :func:`create_engine_from_url` — builds an :class:`~sqlalchemy.ext.
      asyncio.AsyncEngine` for a given URL (SQLite for dev/test, PostgreSQL
      with asyncpg for production).
    * :func:`session_factory` — an async sessionmaker bound to an engine.
    * :func:`get_session` — FastAPI dependency that yields one
      :class:`~sqlalchemy.ext.asyncio.AsyncSession` per request.
    * :func:`init_db` — creates all tables (`CREATE TABLE IF NOT EXISTS`).

The dependency injection layer is kept explicit: tests can substitute the
session factory (e.g. with an in-memory SQLite engine) without touching the
application code.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

# Re-exported so ``from core.database import Base`` is the canonical import.
Base: type[DeclarativeBase] = DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all ORM models.

    The empty subclass guarantees a single, importable, canonical base class
    for metadata collection (``Base.metadata`` powers ``init_db``).
    """


def create_engine_from_url(database_url: str) -> AsyncEngine:
    """Create an async engine from a SQLAlchemy URL.

    Args:
        database_url: For example ``sqlite+aiosqlite:///./advisor.db`` or
            ``postgresql+asyncpg://user:pass@host:5432/db``.

    Returns:
        An :class:`AsyncEngine` instance.

    Raises:
        ImportError: Propagated from SQLAlchemy when the driver for the given
            scheme is not installed.
    """
    engine = create_async_engine(database_url, echo=False, pool_pre_ping=True)

    if database_url.startswith("sqlite"):
        from sqlalchemy import event

        # SQLite, varsayılan olarak FK kısıtlarını uygulamaz; ON DELETE
        # CASCADE'in (ve diğer FK kurallarının) çalışması için her bağlantıda
        # PRAGMA foreign_keys=ON etkinleştirilmeli.
        @event.listens_for(engine.sync_engine, "connect")
        def _enable_sqlite_fk(dbapi_conn, _record):  # noqa: ANN001
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def session_factory_for(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build an async sessionmaker bound to the given engine.

    Args:
        engine: The async engine to bind sessions to.

    Returns:
        An :class:`async_sessionmaker` producing :class:`AsyncSession`
        instances.
    """
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


async def init_db(engine: AsyncEngine) -> None:
    """Create all tables defined on ``Base.metadata`` in the database.

    Intended for application startup and tests. In a real deployment,
    migrations (Alembic) would take over; the schema is kept small here and
    ``create_all`` is deterministic (no-op when tables already exist).

    Args:
        engine: The async engine whose database should be initialised.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session(
    session_factory_holder=session_factory_for,  # overridden at startup
) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an :class:`AsyncSession`.

    The default engine/session is resolved lazily via module-level ``engine``
    and ``SessionFactory`` which the application bootstrap fills in. Tests
    override these with their own engine/sessionmaker.

    Yields:
        An :class:`AsyncSession`. On completion the session is always closed;
        on error it is additionally rolled back.
    """
    session: AsyncSession = session_factory()  # noqa: F821 - resolved at runtime
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


# Module-level holders filled by ``adopt_engine`` during bootstrap.
engine: AsyncEngine | None = None
SessionFactory: async_sessionmaker[AsyncSession] | None = None


def adopt_engine(new_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Register the process-wide engine and session factory.

    Called once from application startup so that ``get_session`` and services
    share the same connection pool.

    Args:
        new_engine: The engine to register.

    Returns:
        The sessionmaker that will be used app-wide.
    """
    global engine, SessionFactory
    engine = new_engine
    SessionFactory = session_factory_for(new_engine)
    return SessionFactory


def session_factory() -> AsyncSession:
    """Return a new session using the registered process-wide factory.

    Raises:
        RuntimeError: If :func:`adopt_engine` has not been called yet.
    """
    if SessionFactory is None:
        raise RuntimeError(
            "Database engine not initialised. Call adopt_engine() in app startup."
        )
    return SessionFactory()
