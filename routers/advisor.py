"""Advisor router — exposes the multi-agent rebalancing workflow via REST.

POST /api/v1/advisor/rebalance/{portfolio_id}?customer_id=…

Works with or without an LLM key: in demo mode (or when the live model
fails) the rationale comes from the deterministic demo renderer and the
response is tagged with ``llm_mode``. There is no 503 path (bug #8).

Dependency injection: :func:`get_advisor_service` returns the app-wide
service (graph compiled once); tests override it with offline doubles.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from core.deps import SessionDep, UserDep, actor_of, load_portfolio_checked
from core.logging import get_logger
from schemas.advisor import AdvisorResponse
from services.advisor_service import AdvisorService
from services.audit import record_audit

logger = get_logger("otonom.router.advisor")
router = APIRouter(prefix="/advisor", tags=["advisor"])


def get_advisor_service(request: Request) -> AdvisorService:
    """FastAPI dependency returning the shared :class:`AdvisorService`."""
    return request.app.state.container.advisor  # type: ignore[no-any-return]


AdvisorServiceDep = Depends(get_advisor_service)


@router.post(
    "/rebalance/{portfolio_id}",
    response_model=AdvisorResponse,
    summary="Portföyü LangGraph ajanlarıyla yeniden dengele",
)
async def rebalance_portfolio(
    portfolio_id: int,
    session: SessionDep,
    user: UserDep,
    customer_id: int = Query(..., gt=0, description="Müşteri kimliği"),
    service: AdvisorService = AdvisorServiceDep,
) -> AdvisorResponse:
    """Run the advisor workflow for a portfolio and persist the rebalance."""
    try:
        await load_portfolio_checked(session, user, portfolio_id)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(status_code=422, detail=exc.detail) from exc
        raise
    result = await service.run_rebalance(portfolio_id=portfolio_id, customer_id=customer_id)
    if result.error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=result.error)
    await record_audit(
        actor=actor_of(user),
        actor_role=user.role,
        action="advisor.rebalance",
        entity_type="portfolio",
        entity_id=portfolio_id,
        customer_id=customer_id,
        payload={
            "orders": len(result.orders),
            "llm_mode": result.llm_mode,
            "run_id": result.run_id,
        },
    )
    return AdvisorResponse.model_validate(result.to_dict())
