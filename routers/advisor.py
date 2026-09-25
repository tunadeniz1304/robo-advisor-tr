"""Advisor & proposal router — the human-in-the-loop rebalancing API.

* ``POST /advisor/rebalance/{portfolio_id}`` — runs the LangGraph workflow
  up to the approval interrupt and returns the proposal (``ONAY_BEKLIYOR``)
  with orders, costs, taxes, risk change and explanation. No order is
  executed here. Works without an LLM key (demo rationale).
* ``POST /proposals/{id}/approve`` — atomic approval + single execution
  (``Idempotency-Key`` header supported; a second approval is a no-op).
* ``POST /proposals/{id}/reject``, ``GET /proposals``, ``GET /proposals/{id}``.
"""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from core.deps import SessionDep, UserDep, actor_of, load_portfolio_checked
from core.logging import get_logger
from models import Customer, RebalanceProposal
from schemas.advisor import AdvisorResponse
from services.advisor_service import AdvisorService
from services.rebalancing.proposals import ProposalError, proposal_to_dict

logger = get_logger("otonom.router.advisor")
router = APIRouter(tags=["advisor"])


def get_advisor_service(request: Request) -> AdvisorService:
    """FastAPI dependency returning the shared :class:`AdvisorService`."""
    return cast(AdvisorService, request.app.state.container.advisor)


AdvisorServiceDep = Depends(get_advisor_service)


class RejectBody(BaseModel):
    reason: str = Field(default="Müşteri reddetti.", max_length=400)


@router.post(
    "/advisor/rebalance/{portfolio_id}",
    response_model=AdvisorResponse,
    summary="Yeniden dengeleme önerisi oluştur (onay bekler)",
)
async def rebalance_portfolio(
    request: Request,
    portfolio_id: int,
    session: SessionDep,
    user: UserDep,
    customer_id: int = Query(..., gt=0, description="Müşteri kimliği"),
    method: str | None = Query(default=None, description="Optimizasyon yöntemi"),
    override_ack: bool = Query(default=False),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=80)] = None,
    service: AdvisorService = AdvisorServiceDep,
) -> AdvisorResponse:
    """Create a proposal through the agent graph; execution needs approval."""
    await load_portfolio_checked(session, user, portfolio_id)  # yok / yabancı → 404
    from services.regime import cached_regime

    regime = await cached_regime(request.app.state.container)
    result = await service.start(
        portfolio_id,
        customer_id,
        actor=actor_of(user),
        actor_role=user.role,
        user_id=user.id,
        method=method,
        override_ack=override_ack,
        idempotency_key=idempotency_key,
        regime={"label": regime.get("label"), "tilt": regime.get("tilt", 0.0)},
    )
    if result.error:
        code = 403 if "uygun değildir" in result.error else status.HTTP_422_UNPROCESSABLE_CONTENT
        raise HTTPException(status_code=code, detail=result.error)
    return AdvisorResponse.model_validate(result.to_dict())


async def _load_checked(session: SessionDep, user: UserDep, proposal_id: int) -> RebalanceProposal:
    proposal = await session.get(RebalanceProposal, proposal_id)
    missing = HTTPException(status_code=404, detail=f"Öneri {proposal_id} bulunamadı.")
    if proposal is None:
        raise missing
    try:
        await load_portfolio_checked(session, user, proposal.portfolio_id)
    except HTTPException as exc:
        raise missing from exc  # başkasının önerisi: var olmayanla aynı yanıt
    return proposal


@router.get("/proposals", summary="Yeniden dengeleme önerileri")
async def list_proposals(
    session: SessionDep,
    user: UserDep,
    portfolio_id: int | None = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict[str, Any]]:
    """List proposals visible to the caller (newest first)."""
    stmt = select(RebalanceProposal).order_by(RebalanceProposal.id.desc())
    if portfolio_id is not None:
        await load_portfolio_checked(session, user, portfolio_id)
        stmt = stmt.where(RebalanceProposal.portfolio_id == portfolio_id)
    elif user.is_advisor:
        stmt = stmt.join(Customer, Customer.id == RebalanceProposal.customer_id).where(
            Customer.advisor_user_id == user.id
        )
    elif user.is_customer:
        stmt = stmt.where(RebalanceProposal.customer_id == (user.customer_id or -1))
    if status_filter:
        stmt = stmt.where(RebalanceProposal.status == status_filter)
    rows = (await session.execute(stmt.limit(limit))).scalars().all()
    return [proposal_to_dict(p) for p in rows]


@router.get("/proposals/{proposal_id}", summary="Öneri detayı")
async def get_proposal(proposal_id: int, session: SessionDep, user: UserDep) -> dict[str, Any]:
    return proposal_to_dict(await _load_checked(session, user, proposal_id))


@router.post("/proposals/{proposal_id}/approve", summary="Öneriyi onayla ve yürüt")
async def approve_proposal(
    proposal_id: int,
    session: SessionDep,
    user: UserDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=80)] = None,
    service: AdvisorService = AdvisorServiceDep,
) -> dict[str, Any]:
    """Approve once; orders are filled exactly once by the simulated broker."""
    await _load_checked(session, user, proposal_id)
    try:
        return await service.approve(
            proposal_id, user_id=user.id, role=user.role, idempotency_key=idempotency_key
        )
    except ProposalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/proposals/{proposal_id}/reject", summary="Öneriyi reddet")
async def reject_proposal(
    proposal_id: int,
    body: RejectBody,
    session: SessionDep,
    user: UserDep,
    service: AdvisorService = AdvisorServiceDep,
) -> dict[str, Any]:
    await _load_checked(session, user, proposal_id)
    try:
        return await service.reject(
            proposal_id, user_id=user.id, role=user.role, reason=body.reason
        )
    except ProposalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
