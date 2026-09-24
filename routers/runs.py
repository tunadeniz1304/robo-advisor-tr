"""Advisor runs router — read-only audit-trail history API.

Every advisory run persists a row in ``advisor_runs`` (see
:mod:`models.advisor_run`). This router exposes that record for regulators,
auditors, and the frontend dashboard — list by portfolio or customer, plus
single-run detail. Write happens only inside the LangGraph workflow; this
endpoint is intentionally read-only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select

from core.deps import SessionDep, UserDep, load_customer_checked, load_portfolio_checked
from models import AdvisorRun, Customer

router = APIRouter(prefix="/runs", tags=["advisor-runs"])


class AdvisorRunOut(BaseModel):
    """Public read model of one advisory run (never returns internal state)."""

    id: int
    portfolio_id: int
    customer_id: int
    market_input: dict[str, Any]
    target_weights: dict[str, Any]
    orders: list[dict[str, Any]]
    report: str
    status: str
    error: str | None
    created_at: datetime | None

    model_config = {"from_attributes": True}


def _run_out(run: AdvisorRun) -> AdvisorRunOut:
    return AdvisorRunOut(
        id=run.id,
        portfolio_id=run.portfolio_id,
        customer_id=run.customer_id,
        market_input=run.market_input or {},
        target_weights=run.target_weights or {},
        orders=run.orders or [],
        report=run.report or "",
        status=run.status,
        error=run.error,
        created_at=run.created_at,
    )


@router.get("", response_model=list[AdvisorRunOut])
async def list_runs(
    session: SessionDep,
    user: UserDep,
    portfolio_id: int | None = Query(default=None),
    customer_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[AdvisorRunOut]:
    """List audit runs, newest first, optionally filtered by portfolio/customer."""
    stmt = select(AdvisorRun).order_by(AdvisorRun.created_at.desc(), AdvisorRun.id.desc())
    if portfolio_id is not None:
        await load_portfolio_checked(session, user, portfolio_id)
        stmt = stmt.where(AdvisorRun.portfolio_id == portfolio_id)
    if customer_id is not None:
        await load_customer_checked(session, user, customer_id)
        stmt = stmt.where(AdvisorRun.customer_id == customer_id)
    if portfolio_id is None and customer_id is None and not user.is_admin:
        if user.is_advisor:
            stmt = stmt.join(Customer, Customer.id == AdvisorRun.customer_id).where(
                Customer.advisor_user_id == user.id
            )
        else:
            stmt = stmt.where(AdvisorRun.customer_id == (user.customer_id or -1))
    stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    return [_run_out(r) for r in result.scalars().all()]


@router.get("/{run_id}", response_model=AdvisorRunOut)
async def get_run(run_id: int, session: SessionDep, user: UserDep) -> AdvisorRunOut:
    """Fetch a single audit run by id (404 if absent)."""
    run = await session.get(AdvisorRun, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"advisor run {run_id} not found"
        )
    await load_portfolio_checked(session, user, run.portfolio_id)
    return _run_out(run)
