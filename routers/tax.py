"""Tax-lot views (informational): open lots, realised P&L, sale and harvest simulation.

All numbers come from replaying the portfolio's ledger through
:class:`services.tax_engine.LotBook`; nothing here executes a trade.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from core.deps import SessionDep, UserDep, load_portfolio_checked
from core.policy import get_policy
from models import Transaction
from services.tax_engine import LOT_METHODS, harvest_simulation, realized_summary, replay

router = APIRouter(prefix="/portfolios", tags=["tax"])

NOTE = "Bilgi amaçlıdır; oranlar politika dosyasındaki örnek tablodandır, vergi tavsiyesi değildir."


async def _book(session: SessionDep, portfolio_id: int, method: str) -> Any:
    rows = (
        (await session.execute(select(Transaction).where(Transaction.portfolio_id == portfolio_id)))
        .scalars()
        .all()
    )
    trades = [
        {
            "id": t.id,
            "symbol": t.ticker,
            "side": t.side,
            "quantity": float(t.quantity),
            "price": float(t.price),
            "fees": float(t.fees),
            "when": t.executed_at,
        }
        for t in rows
    ]
    return replay(trades, method)


def _method(method: str | None) -> str:
    chosen = (method or str(get_policy().rebalance.get("lot_method", "FIFO"))).upper()
    if chosen not in LOT_METHODS:
        raise HTTPException(status_code=422, detail=f"Lot yöntemi FIFO veya HIFO olmalı: {method}")
    return chosen


@router.get("/{portfolio_id}/tax/lots", summary="Açık lotlar ve gerçekleşmemiş kâr/zarar")
async def tax_lots(
    request: Request,
    portfolio_id: int,
    session: SessionDep,
    user: UserDep,
    method: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    await load_portfolio_checked(session, user, portfolio_id)
    chosen = _method(method)
    book, _ = await _book(session, portfolio_id, chosen)
    lots = book.open_lots()
    prices = await request.app.state.container.proposals.current_prices(
        sorted({lot.symbol for lot in lots})
    )
    today = date.today()
    return {
        "portfolio_id": portfolio_id,
        "method": chosen,
        "lots": [
            {
                "lot_id": lot.lot_id,
                "symbol": lot.symbol,
                "quantity": round(lot.quantity, 8),
                "unit_cost": round(lot.unit_cost, 6),
                "acquired": lot.acquired.isoformat(),
                "holding_days": (today - lot.acquired).days,
                "price": prices.get(lot.symbol),
                "unrealized": round((prices[lot.symbol] - lot.unit_cost) * lot.quantity, 2)
                if lot.symbol in prices
                else None,
            }
            for lot in lots
        ],
        "note": NOTE,
    }


@router.get("/{portfolio_id}/tax/realized", summary="Gerçekleşen kâr/zarar ve tahmini stopaj")
async def tax_realized(
    portfolio_id: int,
    session: SessionDep,
    user: UserDep,
    year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
    method: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    await load_portfolio_checked(session, user, portfolio_id)
    chosen = _method(method)
    _, realized = await _book(session, portfolio_id, chosen)
    return {
        "portfolio_id": portfolio_id,
        "method": chosen,
        **realized_summary(realized, year),
        "note": NOTE,
    }


class SaleIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=24)
    quantity: float = Field(gt=0)


@router.post("/{portfolio_id}/tax/simulate-sale", summary="Satış simülasyonu: FIFO ve HIFO")
async def simulate_sale(
    request: Request, portfolio_id: int, body: SaleIn, session: SessionDep, user: UserDep
) -> dict[str, Any]:
    """Realised gain and withholding of a hypothetical sale under both methods."""
    await load_portfolio_checked(session, user, portfolio_id)
    prices = await request.app.state.container.proposals.current_prices([body.symbol])
    if body.symbol not in prices:
        raise HTTPException(status_code=422, detail=f"Fiyat yok: {body.symbol}")
    out: dict[str, Any] = {}
    for method in LOT_METHODS:
        book, _ = await _book(session, portfolio_id, method)
        res = book.sell(
            body.symbol, body.quantity, prices[body.symbol], 0.0, date.today(), dry_run=True
        )
        out[method] = {
            "gain": round(res.gain, 2),
            "tax": round(res.tax(), 2),
            "untracked_quantity": round(res.untracked_quantity, 8),
            "slices": [s.to_dict() for s in res.slices],
        }
    return {
        "portfolio_id": portfolio_id,
        "symbol": body.symbol,
        "price": prices[body.symbol],
        "methods": out,
        "note": NOTE,
    }


@router.get("/{portfolio_id}/tax/harvest", summary="Vergi zararı hasadı simülasyonu (bilgi amaçlı)")
async def tax_harvest_v2(
    request: Request,
    portfolio_id: int,
    session: SessionDep,
    user: UserDep,
    method: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    await load_portfolio_checked(session, user, portfolio_id)
    chosen = _method(method)
    book, realized = await _book(session, portfolio_id, chosen)
    prices = await request.app.state.container.proposals.current_prices(
        sorted({lot.symbol for lot in book.open_lots()})
    )
    return {
        "portfolio_id": portfolio_id,
        "method": chosen,
        **harvest_simulation(book, prices, realized, today=date.today()),
    }
