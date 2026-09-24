"""Portfolio CRUD router (async FastAPI, ownership aware)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from core.deps import (
    CurrentUser,
    SessionDep,
    UserDep,
    actor_of,
    load_customer_checked,
    load_portfolio_checked,
    require_roles,
)
from core.money import to_decimal
from models import CashFlow, Customer, Portfolio
from models.base import utcnow
from schemas.portfolio import PortfolioCreate, PortfolioRead, PortfolioUpdate
from services.analytics.engine import EXTERNAL_FLOW_KINDS, FLOW_KINDS, FlowEvent
from services.audit import record_audit

router = APIRouter(prefix="/portfolios", tags=["portfolios"])

StaffDep = Annotated[CurrentUser, require_roles("admin", "danisman")]


@router.post("", response_model=PortfolioRead, status_code=status.HTTP_201_CREATED)
async def create_portfolio(
    payload: PortfolioCreate, session: SessionDep, user: StaffDep
) -> Portfolio:
    """Create a new portfolio for an existing customer (staff)."""
    await load_customer_checked(session, user, payload.customer_id)
    data = payload.model_dump()
    data["cash"] = to_decimal(data["cash"])
    portfolio = Portfolio(**data)
    session.add(portfolio)
    await session.commit()
    await session.refresh(portfolio)
    return portfolio


@router.get("", response_model=list[PortfolioRead])
async def list_portfolios(
    session: SessionDep,
    user: UserDep,
    customer_id: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Portfolio]:
    """List portfolios visible to the caller, optionally filtered by customer."""
    stmt = select(Portfolio).order_by(Portfolio.id)
    if user.is_advisor:
        stmt = stmt.join(Customer, Customer.id == Portfolio.customer_id).where(
            Customer.advisor_user_id == user.id
        )
    elif user.is_customer:
        stmt = stmt.where(Portfolio.customer_id == (user.customer_id or -1))
    if customer_id is not None:
        stmt = stmt.where(Portfolio.customer_id == customer_id)
    result = await session.execute(stmt.limit(limit).offset(offset))
    return list(result.scalars().all())


@router.get("/{portfolio_id}", response_model=PortfolioRead)
async def get_portfolio(portfolio_id: int, session: SessionDep, user: UserDep) -> Portfolio:
    """Fetch a single portfolio by id."""
    return await load_portfolio_checked(session, user, portfolio_id)  # type: ignore[no-any-return]


@router.put("/{portfolio_id}", response_model=PortfolioRead)
async def update_portfolio(
    portfolio_id: int, payload: PortfolioUpdate, session: SessionDep, user: UserDep
) -> Portfolio:
    """Update portfolio metadata; cash/holdings corrections are staff-only."""
    portfolio = await load_portfolio_checked(session, user, portfolio_id)
    updates = payload.model_dump(exclude_unset=True)
    if ({"cash", "holdings"} & updates.keys()) and user.is_customer:
        raise HTTPException(
            status_code=403,
            detail="Nakit ve pozisyonlar yalnızca işlem ve onaylı önerilerle değişir.",
        )
    if "cash" in updates:
        updates["cash"] = to_decimal(updates["cash"])
        # Nakit düzeltmesi getiri değildir: ledger'a dış akış olarak yazılır (TWR'ı etkilemez).
        delta = updates["cash"] - to_decimal(portfolio.cash)
        if delta != 0:
            session.add(
                CashFlow(
                    portfolio_id=portfolio.id,
                    kind="DEPOSIT" if delta > 0 else "WITHDRAWAL",
                    amount=abs(delta),
                    currency=portfolio.currency,
                    note="manuel nakit düzeltmesi",
                )
            )
    for field, value in updates.items():
        setattr(portfolio, field, value)
    await session.commit()
    await session.refresh(portfolio)
    return portfolio  # type: ignore[no-any-return]


@router.delete("/{portfolio_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_portfolio(portfolio_id: int, session: SessionDep, user: StaffDep) -> None:
    """Delete a portfolio and its transactions (staff)."""
    portfolio = await load_portfolio_checked(session, user, portfolio_id)
    await session.delete(portfolio)
    await session.commit()


class CashFlowIn(BaseModel):
    """A ledger cash movement (``amount`` is a positive magnitude)."""

    kind: str = Field(pattern="^(" + "|".join(sorted(FLOW_KINDS)) + ")$")
    amount: float = Field(gt=0)
    occurred_at: datetime | None = None
    note: str | None = Field(default=None, max_length=500)


def _flow_out(row: CashFlow) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind,
        "amount": round(float(row.amount), 2),
        "currency": row.currency,
        "note": row.note,
        "occurred_at": row.occurred_at.isoformat(),
        "external": row.kind in EXTERNAL_FLOW_KINDS,
    }


@router.post(
    "/{portfolio_id}/cash-flows",
    status_code=status.HTTP_201_CREATED,
    summary="Nakit akışı kaydı (yatırma, çekme, temettü, kupon, faiz, ücret, vergi)",
)
async def add_cash_flow(
    portfolio_id: int, payload: CashFlowIn, session: SessionDep, user: StaffDep
) -> dict[str, Any]:
    """Record a cash flow and update cash in one transaction.

    Only DEPOSIT/WITHDRAWAL are external flows for the TWR; dividends,
    coupons, interest, fees, taxes and commissions are portfolio returns.
    """
    portfolio = await load_portfolio_checked(session, user, portfolio_id)
    delta = Decimal(str(FlowEvent(utcnow().date(), payload.kind, payload.amount).cash_delta))
    new_cash = to_decimal(portfolio.cash) + delta
    if new_cash < 0:
        raise HTTPException(status_code=409, detail="Yetersiz nakit: bu çıkış kaydedilemez.")
    row = CashFlow(
        portfolio_id=portfolio.id,
        kind=payload.kind,
        amount=to_decimal(payload.amount),
        currency=portfolio.currency,
        note=payload.note,
        occurred_at=payload.occurred_at or utcnow(),
    )
    portfolio.cash = to_decimal(new_cash)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    await record_audit(
        actor=actor_of(user),
        actor_role=user.role,
        action="cash_flow.recorded",
        entity_type="portfolio",
        entity_id=portfolio.id,
        customer_id=portfolio.customer_id,
        payload={"kind": payload.kind, "amount": payload.amount},
    )
    return {**_flow_out(row), "cash_after": round(float(portfolio.cash), 2)}


@router.get("/{portfolio_id}/cash-flows", summary="Nakit akışları (ledger)")
async def list_cash_flows(
    portfolio_id: int, session: SessionDep, user: UserDep
) -> list[dict[str, Any]]:
    await load_portfolio_checked(session, user, portfolio_id)
    rows = (
        (
            await session.execute(
                select(CashFlow)
                .where(CashFlow.portfolio_id == portfolio_id)
                .order_by(CashFlow.occurred_at, CashFlow.id)
            )
        )
        .scalars()
        .all()
    )
    return [_flow_out(r) for r in rows]
