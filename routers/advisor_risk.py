"""Risk report router — exposure of the dynamic risk assessment via REST.

GET /api/v1/advisor/risk/{customer_id}

Returns the current dynamic risk score, category, equity ceiling and the
rationale for a customer. This is a read-only, deterministic computation
(reuses :class:`services.risk_service.RiskService`) and is deliberately
separate from the advisory rebalance endpoint so risk can be surfaced
before any rebalancing decision.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from core.deps import SessionDep, UserDep, load_customer_checked
from services.risk_service import RiskService

router = APIRouter(prefix="/advisor", tags=["advisor"])


class RiskReport(BaseModel):
    """Snapshot of a customer's dynamic risk profile."""

    customer_id: int
    score: float
    category: str
    max_equity_weight: float
    rationale: str


@router.get(
    "/risk/{customer_id}",
    response_model=RiskReport,
    summary="Müşterinin dinamik risk profilini döndür",
)
async def get_risk_report(customer_id: int, session: SessionDep, user: UserDep) -> RiskReport:
    """Compute and return the dynamic risk assessment of a customer.

    The score is derived from the customer's declared tolerance, investment
    horizon and monthly income (see :mod:`services.risk_service`).
    """
    customer = await load_customer_checked(session, user, customer_id)
    assessment = RiskService().assess(customer)
    return RiskReport(
        customer_id=customer.id,
        score=round(assessment.score, 2),
        category=assessment.category,
        max_equity_weight=assessment.max_equity_weight,
        rationale=assessment.rationale,
    )
