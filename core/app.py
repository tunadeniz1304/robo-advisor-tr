"""FastAPI application factory.

``create_app`` assembles the whole application for a given
:class:`~core.config.Settings` and registers:

    * health/version endpoints,
    * CRUD routers (customers, portfolios, transactions) — Adım 3,
    * the advisor router exposing the LangGraph orchestration — Adım 4.

Because everything is assembled through a factory, tests can build an isolated
app with an in-memory database and deterministic stubs without touching global
state. The module-level ``app`` singleton is what uvicorn (root ``main.py``)
serves in production.
"""
from __future__ import annotations

from typing import AsyncIterator

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from core.config import Settings
from core.database import adopt_engine, init_db
from core.logging import LOGGER
from routers import customers, portfolios, transactions


def _create_db_engine(settings: Settings) -> AsyncEngine:
    """Build the engine for the configured database URL."""
    from core.database import create_engine_from_url

    return create_engine_from_url(settings.database_url)


async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown lifecycle: init DB engine + create tables, then log."""
    settings: Settings = app.state.settings
    engine = _create_db_engine(settings)
    app.state.engine = engine
    adopt_engine(engine)
    await init_db(engine)
    LOGGER.info(
        "application_startup",
        version=settings.version,
        database=settings.database_url.split("://")[0],
        llm_provider=settings.llm_provider_name,
    )
    try:
        yield
    finally:
        await engine.dispose()
        LOGGER.info("application_shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI application instance.

    Args:
        settings: Optional settings override (used by tests). When ``None``
            the default :func:`core.config.Settings.load` is used.

    Returns:
        A fully configured :class:`FastAPI` application.
    """
    if settings is None:
        settings = Settings.load()

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description=(
            "Bankacılık sektörü için LangGraph tabanlı Otonom Finansal Danışman "
            "(Robo-Advisor). Gerçek LLM (OpenAI/Anthropic) ve gerçek piyasa "
            "verisi (Yahoo Finance) kullanır."
        ),
        lifespan=_lifespan,
    )
    app.state.settings = settings

    # --- Health check --------------------------------------------------------
    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        """Liveness probe used by Docker healthcheck and uptime monitors."""
        return {"status": "ok", "service": settings.app_name, "version": settings.version}

    @app.get(settings.api_v1_prefix + "/health", tags=["system"])
    async def health_v1() -> dict[str, str]:
        """Versioned health probe."""
        return {"status": "ok", "version": settings.version}

    # --- CRUD routers (Adım 3) ----------------------------------------------
    api_prefix = settings.api_v1_prefix
    app.include_router(customers.router, prefix=api_prefix)
    app.include_router(portfolios.router, prefix=api_prefix)
    app.include_router(transactions.router, prefix=api_prefix)

    # --- Advisor router (Adım 4) --------------------------------------------
    from routers import advisor

    app.include_router(advisor.router, prefix=api_prefix)

    # --- Eklemeler (sürekli geliştirme): analytics + risk raporu -------------
    from routers import analytics, advisor_risk, runs

    app.include_router(analytics.router, prefix=api_prefix)
    app.include_router(advisor_risk.router, prefix=api_prefix)
    app.include_router(runs.router, prefix=api_prefix)

    return app
