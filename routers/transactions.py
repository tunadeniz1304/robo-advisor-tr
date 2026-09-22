"""Transaction CRUD/router (async FastAPI).

Transactions are primarily created by the Portfolio Manager's rebalancing
workflow (reason="rebalance"); this router also exposes manual placement and
read-only ledger queries.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_session
from models import Portfolio, Transaction
from schemas.transaction import TransactionCreate, TransactionRead, Side

router = APIRouter(prefix="/transactions", tags=["transactions"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("", response_model=TransactionRead, status_code=status.HTTP_201_CREATED)
async def create_transaction(
    payload: TransactionCreate, session: SessionDep
) -> Transaction:
    """Place a manual transaction on an existing portfolio."""
    portfolio = await session.get(Portfolio, payload.portfolio_id)
    if portfolio is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Portfolio {payload.portfolio_id} bulunamadı.",
        )
    transaction = Transaction(
        portfolio_id=payload.portfolio_id,
        ticker=payload.ticker.upper(),
        side=payload.side.value if isinstance(payload.side, Side) else str(payload.side),
        quantity=payload.quantity,
        price=payload.price,
        total_amount=round(payload.quantity * payload.price, 4),
        reason=payload.reason,
    )
    session.add(transaction)
    await session.commit()
    await session.refresh(transaction)
    return transaction


@router.get("", response_model=list[TransactionRead])
async def list_transactions(
    session: SessionDep,
    portfolio_id: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Transaction]:
    """List transactions, optionally filtered by portfolio_id."""
    stmt = select(Transaction).order_by(Transaction.executed_at.desc())
    if portfolio_id is not None:
        stmt = stmt.where(Transaction.portfolio_id == portfolio_id)
    result = await session.execute(stmt.limit(limit).offset(offset))
    return list(result.scalars().all())


@router.get("/{transaction_id}", response_model=TransactionRead)
async def get_transaction(transaction_id: int, session: SessionDep) -> Transaction:
    """Fetch a single transaction by id."""
    transaction = await session.get(Transaction, transaction_id)
    if transaction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transaction {transaction_id} bulunamadı.",
        )
    return transaction
