"""LLM status router — mode, model and health counters (never the key)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(prefix="/llm", tags=["llm"])


@router.get("/status", summary="LLM modu ve sağlık durumu")
async def llm_status(request: Request) -> dict[str, Any]:
    """Return ``{mode, model, base_url_host, key_present, last_latency_ms, calls, failures}``.

    Public on purpose (UI badge "AI: Canlı / Demo"); contains no secrets.
    """
    return request.app.state.container.gateway.status()  # type: ignore[no-any-return]
