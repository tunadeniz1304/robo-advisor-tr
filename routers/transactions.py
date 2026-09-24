"""Transaction router.

Manual trades go through :func:`services.ledger.apply_trade`, which updates
holdings, cash and tax lots atomically with the ledger row (bug #6: manual
transactions previously did not touch holdings/cash).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from core.deps import SessionDep, UserDep, actor_of, load_portfolio_checked
from models import Customer, Portfolio, Transaction
from schemas.transaction import TransactionCreate, TransactionRead
from services.audit import record_audit
from services.ledger import LedgerError, apply_trade

router = APIRouter(prefix="/transactions", tags=["transactions"])


@router.post("", response_model=TransactionRead, status_code=status.HTTP_201_CREATED)
async def create_transaction(
    payload: TransactionCreate, session: SessionDep, user: UserDep
) -> Transaction:
    """Place a manual transaction; holdings and cash update atomically."""
    portfolio = await load_portfolio_checked(session, user, payload.portfolio_id)
    try:
        result = await apply_trade(
            session,
            portfolio,
            symbol=payload.ticker.upper(),
            side=payload.side.value,
            quantity=payload.quantity,
            price=payload.price,
            fees=payload.fees,
            reason=payload.reason,
        )
    except LedgerError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    await session.commit()
    await session.refresh(result.transaction)
    tx = result.transaction
    await record_audit(
        actor=actor_of(user),
        actor_role=user.role,
        action="trade.manual",
        entity_type="transaction",
        entity_id=tx.id,
        customer_id=portfolio.customer_id,
        payload={
            "symbol": tx.ticker,
            "side": tx.side,
            "quantity": float(tx.quantity),
            "price": float(tx.price),
        },
    )
    return tx


@router.get("", response_model=list[TransactionRead])
async def list_transactions(
    session: SessionDep,
    user: UserDep,
    portfolio_id: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Transaction]:
    """List transactions visible to the caller, optionally by portfolio."""
    stmt = select(Transaction).order_by(Transaction.executed_at.desc(), Transaction.id.desc())
    if portfolio_id is not None:
        await load_portfolio_checked(session, user, portfolio_id)
        stmt = stmt.where(Transaction.portfolio_id == portfolio_id)
    elif not user.is_admin:
        stmt = stmt.join(Portfolio, Portfolio.id == Transaction.portfolio_id).join(
            Customer, Customer.id == Portfolio.customer_id
        )
        if user.is_advisor:
            stmt = stmt.where(Customer.advisor_user_id == user.id)
        else:
            stmt = stmt.where(Customer.id == (user.customer_id or -1))
    result = await session.execute(stmt.limit(limit).offset(offset))
    return list(result.scalars().all())


@router.get("/{transaction_id}", response_model=TransactionRead)
async def get_transaction(transaction_id: int, session: SessionDep, user: UserDep) -> Transaction:
    """Fetch a single transaction by id."""
    transaction = await session.get(Transaction, transaction_id)
    if transaction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transaction {transaction_id} bulunamadı.",
        )
    await load_portfolio_checked(session, user, transaction.portfolio_id)
    return transaction
