"""HTTP middleware and exception handlers.

* :class:`RequestContextMiddleware` — assigns/propagates ``X-Request-ID``,
  binds it to structlog context vars, records Prometheus metrics, applies the
  per-client rate limit and adds security headers.
* :func:`install_exception_handlers` — 500 responses never leak internal
  exception text; they carry a correlation id instead.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from limits import parse
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from core.logging import get_logger
from core.metrics import HTTP_LATENCY, HTTP_REQUESTS

logger = get_logger("otonom.http")

REQUEST_ID_HEADER = "X-Request-ID"

# Sayfa (HTML) için CSP: yalnızca kendi kaynaklarımız; inline script yok.
PAGE_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)
API_CSP = "default-src 'none'; frame-ancestors 'none'"
DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


class RateLimiter:
    """Per-app moving-window rate limiter (``limits`` library, in memory)."""

    def __init__(self, default_limit: str, enabled: bool = True) -> None:
        self.enabled = enabled
        self._storage = MemoryStorage()
        self._limiter = MovingWindowRateLimiter(self._storage)
        self._default = parse(default_limit)

    def hit(self, key: str, limit: str | None = None) -> bool:
        """Consume one unit; returns ``False`` when the limit is exceeded."""
        if not self.enabled:
            return True
        item = parse(limit) if limit else self._default
        return bool(self._limiter.hit(item, key))

    def reset(self) -> None:
        self._storage.reset()


def client_key(request: Request) -> str:
    """Rate limit key: client IP (first ``X-Forwarded-For`` hop if present)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "anon"


def _route_label(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return str(path) if path else "unmatched"


def _error_body(detail: Any, request_id: str) -> dict[str, Any]:
    return {"detail": detail, "correlation_id": request_id}


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Request id, structured log context, metrics, rate limit and headers."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        limiter: RateLimiter | None = getattr(request.app.state, "rate_limiter", None)
        path = request.url.path
        exempt = path.startswith(("/static", "/health", "/metrics", "/assets")) or path == "/"
        if limiter is not None and not exempt and not limiter.hit(f"global:{client_key(request)}"):
            response: Response = JSONResponse(
                status_code=429,
                content=_error_body(
                    "Çok fazla istek. Lütfen biraz sonra tekrar deneyin.", request_id
                ),
            )
        else:
            started = time.perf_counter()
            response = await call_next(request)
            elapsed = time.perf_counter() - started
            route = _route_label(request)
            HTTP_REQUESTS.labels(request.method, route, str(response.status_code)).inc()
            HTTP_LATENCY.labels(request.method, route).observe(elapsed)

        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        content_type = response.headers.get("content-type", "")
        if path.startswith(DOCS_PATHS):
            pass  # Swagger UI CDN kaynakları kullanır; CSP uygulanmaz.
        elif content_type.startswith("text/html"):
            response.headers.setdefault("Content-Security-Policy", PAGE_CSP)
        else:
            response.headers.setdefault("Content-Security-Policy", API_CSP)
        return response


def install_exception_handlers(app: FastAPI) -> None:
    """Register JSON error handlers that never leak internals."""

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        rid = getattr(request.state, "request_id", "")
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.detail, rid),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        rid = getattr(request.state, "request_id", "")
        errors = [
            {"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")}
            for e in exc.errors()
        ]
        return JSONResponse(status_code=422, content=_error_body(errors, rid))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        rid = getattr(request.state, "request_id", "") or uuid.uuid4().hex
        logger.exception("unhandled_error", path=request.url.path, error_type=type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content=_error_body("Beklenmeyen bir hata oluştu. Destek için kimliği iletin.", rid),
            headers={REQUEST_ID_HEADER: rid},
        )


__all__ = [
    "RateLimiter",
    "RequestContextMiddleware",
    "client_key",
    "install_exception_handlers",
]
