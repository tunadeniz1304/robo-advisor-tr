"""Portfolio CRUD router (async FastAPI, ownership aware)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from core.deps import (
    CurrentUser,
    SessionDep,
    UserDep,
    load_customer_checked,
    load_portfolio_checked,
    require_roles,
)
from core.money import to_decimal
from models import Customer, Portfolio
from schemas.portfolio import PortfolioCreate, PortfolioRead, PortfolioUpdate

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
