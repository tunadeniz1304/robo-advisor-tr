"""LLM status router — mode, model and health counters (never the key)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from core.deps import UserDep

router = APIRouter(prefix="/llm", tags=["llm"])

CUSTOMER_FIELDS = ("mode",)


@router.get("/status", summary="LLM modu ve sağlık durumu")
async def llm_status(request: Request, user: UserDep) -> dict[str, Any]:
    """Return ``{mode, model, key_present, last_latency_ms, calls, failures}``.

    Requires authentication. The upstream host (infrastructure detail)
    and model configuration are shown to staff (admin/danışman) only;
    customers get just the mode for the "AI: Canlı / Demo" badge.
    """
    status: dict[str, Any] = dict(request.app.state.container.gateway.status())
    if not (user.is_admin or user.is_advisor):
        return {k: v for k, v in status.items() if k in CUSTOMER_FIELDS}
    return status
