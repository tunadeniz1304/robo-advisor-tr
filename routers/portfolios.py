"""Portfolio CRUD router (async FastAPI)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_session
from models import Customer, Portfolio
from schemas.portfolio import PortfolioCreate, PortfolioRead, PortfolioUpdate

router = APIRouter(prefix="/portfolios", tags=["portfolios"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def _get_or_404(session: AsyncSession, portfolio_id: int) -> Portfolio:
    portfolio = await session.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Portfolio {portfolio_id} bulunamadı.",
        )
    return portfolio


@router.post("", response_model=PortfolioRead, status_code=status.HTTP_201_CREATED)
async def create_portfolio(payload: PortfolioCreate, session: SessionDep) -> Portfolio:
    """Create a new portfolio for an existing customer."""
    customer = await session.get(Customer, payload.customer_id)
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Customer {payload.customer_id} bulunamadı.",
        )
    portfolio = Portfolio(**payload.model_dump())
    session.add(portfolio)
    await session.commit()
    await session.refresh(portfolio)
    return portfolio


@router.get("", response_model=list[PortfolioRead])
async def list_portfolios(
    session: SessionDep,
    customer_id: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Portfolio]:
    """List portfolios, optionally filtered by customer_id."""
    stmt = select(Portfolio).order_by(Portfolio.id)
    if customer_id is not None:
        stmt = stmt.where(Portfolio.customer_id == customer_id)
    result = await session.execute(stmt.limit(limit).offset(offset))
    return list(result.scalars().all())


@router.get("/{portfolio_id}", response_model=PortfolioRead)
async def get_portfolio(portfolio_id: int, session: SessionDep) -> Portfolio:
    """Fetch a single portfolio by id."""
    return await _get_or_404(session, portfolio_id)


@router.put("/{portfolio_id}", response_model=PortfolioRead)
async def update_portfolio(
    portfolio_id: int, payload: PortfolioUpdate, session: SessionDep
) -> Portfolio:
    """Update portfolio metadata (name, currency, cash, holdings)."""
    portfolio = await _get_or_404(session, portfolio_id)
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(portfolio, field, value)
    await session.commit()
    await session.refresh(portfolio)
    return portfolio


@router.delete("/{portfolio_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_portfolio(portfolio_id: int, session: SessionDep) -> None:
    """Delete a portfolio and its transactions."""
    portfolio = await _get_or_404(session, portfolio_id)
    await session.delete(portfolio)
    await session.commit()
