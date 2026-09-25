"""System endpoints: liveness/readiness probes, version and Prometheus metrics."""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text

from core.database import session_factory
from core.deps import UserDep
from core.metrics import render_metrics

router = APIRouter(tags=["system"])
api_router = APIRouter(tags=["system"])


@router.get("/health")
@router.get("/health/live")
async def health_live(request: Request) -> dict[str, str]:
    """Liveness probe: the process is up."""
    settings = request.app.state.settings
    return {"status": "ok", "service": settings.app_name, "version": settings.version}


async def _readiness(request: Request) -> tuple[bool, dict[str, Any]]:
    checks: dict[str, Any] = {}
    ok = True
    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"hata: {type(exc).__name__}"
        ok = False
    container = request.app.state.container
    checks["llm_mode"] = container.gateway.mode
    status_fn = getattr(container.market, "data_status", None)
    checks["market_data"] = status_fn() if callable(status_fn) else "ok"
    scheduler = container.extras.get("scheduler")
    checks["scheduler"] = (
        "running" if scheduler is not None and getattr(scheduler, "running", False) else "kapalı"
    )
    return ok, checks


@router.get("/health/ready")
async def health_ready(request: Request) -> JSONResponse:
    """Readiness probe: DB reachable, data source and scheduler status."""
    ok, checks = await _readiness(request)
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ok" if ok else "degraded", "checks": checks},
    )


@api_router.get("/health")
async def health_v1(request: Request) -> dict[str, str]:
    """Versioned health probe."""
    return {"status": "ok", "version": request.app.state.settings.version}


@api_router.get("/data/quality", summary="Veri kalitesi (boşluk, sıçrama, bölünme, bayatlık)")
async def data_quality(request: Request, user: UserDep) -> dict[str, Any]:
    """Quality checks of every served price series with its provenance."""
    report: dict[str, Any] = await request.app.state.container.market.quality_report()
    return report


@api_router.get("/system/status", summary="Arayüz rozetleri: veri, veri kalitesi, AI modu")
async def system_status(request: Request, user: UserDep) -> dict[str, Any]:
    """Real state behind the UI badges (data mode, quality, AI mode)."""
    container = request.app.state.container
    quality = await container.market.quality_report()
    return {
        "data": container.market.data_mode(),
        "quality": {"status": quality["status"], "counts": quality["counts"]},
        "ai": {"mode": container.gateway.mode},
    }


def _metrics_allowed(request: Request) -> bool:
    settings = request.app.state.settings
    if settings.metrics_public:
        return True
    token = settings.metrics_token
    header = request.headers.get("authorization", "")
    if not token or not header.lower().startswith("bearer "):
        return False
    return hmac.compare_digest(header[7:].strip().encode(), token.encode())


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    """Prometheus exposition endpoint.

    Private by default: served only with ``METRICS_PUBLIC=true`` or the
    ``METRICS_TOKEN`` bearer token; otherwise it does not exist (404).
    """
    if not _metrics_allowed(request):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    payload, content_type = render_metrics()
    return Response(content=payload, media_type=content_type)
