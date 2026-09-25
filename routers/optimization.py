"""Optimisation router: target allocation preview for a customer."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from core.deps import SessionDep, UserDep, actor_of, load_customer_checked
from models import BLView
from models.base import utcnow
from services.audit import record_audit
from services.optimization.service import METHODS, OptimizationRequest, OptimizationService, View
from services.optimization.strategies import OptimizationError
from services.suitability.service import effective_level

router = APIRouter(tags=["optimization"])


class OptimizeBody(BaseModel):
    method: str | None = Field(
        default=None, description="hrp | black_litterman | min_cvar | risk_parity | mean_variance"
    )
    model_level: int | None = Field(default=None, ge=1, le=10)
    exclude: list[str] = Field(default_factory=list, max_length=30)
    esg_only: bool = False
    override_ack: bool = Field(
        default=False, description="Uygun olmayan dağılımı bilerek kabul ediyorum."
    )


def optimizer_of(request: Request) -> OptimizationService:
    return cast(OptimizationService, request.app.state.container.optimizer)


async def approved_views(session: SessionDep) -> list[View]:
    """Approved, unexpired Black-Litterman house views."""
    now = utcnow()
    rows = (
        (await session.execute(select(BLView).where(BLView.status == "onaylandi"))).scalars().all()
    )
    return [
        View(r.symbol, r.expected_return, r.confidence, r.rationale)
        for r in rows
        if r.valid_until is None or r.valid_until > now
    ]


@router.get("/optimization/methods", summary="Optimizasyon yöntemleri")
async def methods(user: UserDep) -> dict[str, str]:
    return METHODS


@router.post("/customers/{customer_id}/optimize", summary="Hedef dağılım önizlemesi")
async def optimize(
    request: Request, customer_id: int, body: OptimizeBody, session: SessionDep, user: UserDep
) -> dict[str, Any]:
    """Run the optimiser for the customer's effective risk level."""
    customer = await load_customer_checked(session, user, customer_id)
    level = await effective_level(session, customer)
    req = OptimizationRequest(
        level=level.level,
        method=body.method,
        model_level=body.model_level,
        exclude=[s.upper() if s.endswith(".is") else s for s in body.exclude],
        esg_only=body.esg_only,
        override_ack=body.override_ack,
        views=await approved_views(session),
    )
    try:
        result = await optimizer_of(request).optimize(req)
    except OptimizationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not result.gate.allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": "Bu dağılım risk seviyeniz için uygun değildir.",
                "violations": result.gate.violations,
                "hint": "Bilerek devam etmek için override_ack=true gönderin (kayda alınır).",
            },
        )
    if result.gate.overridden:
        await record_audit(
            actor=actor_of(user),
            actor_role=user.role,
            action="suitability.override",
            entity_type="customer",
            entity_id=customer.id,
            customer_id=customer.id,
            payload={"violations": result.gate.violations, "model_level": result.model_level},
        )
    return {"effective_level": level.to_dict(), **result.to_dict()}
