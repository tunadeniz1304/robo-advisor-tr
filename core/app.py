"""FastAPI application factory.

``create_app`` assembles the whole application for a given
:class:`~core.config.Settings`:

    * configuration validation (fail fast, Turkish messages),
    * middleware (request id, metrics, rate limit, security headers, CORS),
    * JSON error handlers that never leak internals,
    * the service container (shared market cache, LLM gateway, advisor graph),
    * all routers under ``/api/v1`` plus the dashboard.

Tests build isolated apps with injected doubles (``market_source``,
``llm_client``) and a temporary database.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from core.config import Settings
from core.container import Container, build_container
from core.crypto import configure_encryption
from core.database import adopt_engine, create_engine_from_url, init_db, session_factory
from core.logging import LOGGER
from core.middleware import RateLimiter, RequestContextMiddleware, install_exception_handlers
from llm.clients import LLMClient
from services.market_service import MarketDataSource
from services.reference_data import seed_reference_data

DEV_ADMIN_PW = "Admin!2345"


async def _bootstrap_admin(settings: Settings) -> None:
    """Create the first admin account when the users table is empty."""
    from core.security import hash_password
    from models import User

    async with session_factory() as session:
        count = (await session.execute(select(func.count(User.id)))).scalar_one()
        if count:
            return
        password = settings.bootstrap_admin_password
        if not password:
            if settings.is_production:
                LOGGER.warning("admin_bootstrap_skipped", reason="ADMIN_PASSWORD tanımlı değil")
                return
            password = DEV_ADMIN_PW
            LOGGER.warning(
                "admin_bootstrap_dev_password", username=settings.bootstrap_admin_username
            )
        session.add(
            User(
                username=settings.bootstrap_admin_username,
                password_hash=hash_password(password),
                role="admin",
                display_name="Sistem Yöneticisi",
            )
        )
        await session.commit()


def _log_llm_startup(container: Container) -> None:
    settings = container.settings
    if container.gateway.mode == "live":
        LOGGER.info(
            "llm_startup",
            llm_mode="live",
            model=settings.llm_model,
            host=settings.llm_base_url_host,
        )
    else:
        reason = "forced_demo" if settings.llm_mode == "demo" else "no_api_key"
        LOGGER.info("llm_startup", llm_mode="demo", reason=reason)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown: DB engine + schema, admin bootstrap, scheduler."""
    settings: Settings = app.state.settings
    container: Container = app.state.container
    engine = create_engine_from_url(settings.database_url)
    app.state.engine = engine
    adopt_engine(engine)
    await init_db(engine, settings)
    await seed_reference_data()
    await _bootstrap_admin(settings)
    if settings.seed_demo:
        from services.demo_seed import seed_demo

        await seed_demo(container)
    _log_llm_startup(container)
    if settings.scheduler_enabled:
        from core.scheduler import build_scheduler

        scheduler = build_scheduler(container)
        scheduler.start()
        container.extras["scheduler"] = scheduler
    LOGGER.info(
        "application_startup",
        version=settings.version,
        environment=settings.environment,
        database=settings.database_url.split("://")[0],
    )
    try:
        yield
    finally:
        scheduler = container.extras.pop("scheduler", None)
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        await container.aclose()
        await engine.dispose()
        LOGGER.info("application_shutdown")


def create_app(
    settings: Settings | None = None,
    *,
    market_source: MarketDataSource | None = None,
    llm_client: LLMClient | None = None,
) -> FastAPI:
    """Build and configure the FastAPI application instance.

    Args:
        settings: Optional settings override (tests). ``None`` → env.
        market_source: Optional market data source override (offline tests).
        llm_client: Optional LLM client override (tests: fake/failing LLM).

    Returns:
        A fully configured :class:`FastAPI` application.

    Raises:
        ConfigurationError: When the configuration is inconsistent.
    """
    if settings is None:
        settings = Settings.load()
    settings.validate()
    configure_encryption(
        settings.pii_encryption_key, allow_dev_key=settings.environment in ("dev", "test")
    )

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description=(
            "Dijital varlık yönetimi (robo-advisor) prototipi: uygunluk testi, çoklu "
            "varlık optimizasyonu, hedef bazlı planlama, öneri→onay→yürütme ve "
            "veriyle topraklanmış LLM yorumu. Bilgi amaçlıdır, yatırım tavsiyesi değildir."
        ),
        lifespan=_lifespan,
    )
    app.state.settings = settings
    app.state.container = build_container(
        settings, market_source=market_source, llm_client=llm_client
    )
    app.state.rate_limiter = RateLimiter(
        settings.rate_limit_default, enabled=settings.rate_limit_enabled
    )

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    install_exception_handlers(app)

    from routers import (
        advisor,
        advisor_risk,
        analytics,
        audit,
        auth,
        copilot,
        customers,
        goals,
        insights,
        llm,
        market,
        optimization,
        portfolios,
        reports,
        runs,
        suitability,
        system,
        transactions,
    )

    app.include_router(system.router)
    api_prefix = settings.api_v1_prefix
    for module in (
        auth,
        customers,
        portfolios,
        transactions,
        advisor,
        advisor_risk,
        analytics,
        runs,
        llm,
        market,
        audit,
        suitability,
        optimization,
        goals,
        reports,
        insights,
        copilot,
    ):
        app.include_router(module.router, prefix=api_prefix)
    app.include_router(system.api_router, prefix=api_prefix)

    # --- Frontend -------------------------------------------------------------
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
    if frontend_dir.exists():
        app.mount("/assets", StaticFiles(directory=frontend_dir), name="assets")

        @app.get("/", include_in_schema=False)
        async def dashboard() -> FileResponse:
            """Serve the single-page application."""
            return FileResponse(frontend_dir / "index.html")

    return app


__all__ = ["create_app"]
