"""LLM status router — mode, model and health counters (never the key)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from core.deps import UserDep

router = APIRouter(prefix="/llm", tags=["llm"])

STAFF_ONLY_FIELDS = ("base_url_host",)


@router.get("/status", summary="LLM modu ve sağlık durumu")
async def llm_status(request: Request, user: UserDep) -> dict[str, Any]:
    """Return ``{mode, model, key_present, last_latency_ms, calls, failures}``.

    Requires authentication. The upstream host is infrastructure detail and is
    shown to staff (admin/danışman) only; customers get the mode for the
    "AI: Canlı / Demo" badge.
    """
    status: dict[str, Any] = dict(request.app.state.container.gateway.status())
    if not (user.is_admin or user.is_advisor):
        for key in STAFF_ONLY_FIELDS:
            status.pop(key, None)
    return status
