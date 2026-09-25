"""Portfolio value history and the performance report, read from the ledger.

Thin database adapter around :mod:`services.analytics.engine`: it loads the
transactions and cash flows of a portfolio, turns them into engine events and
returns the engine's labelled metrics. Every endpoint that shows performance
(``/report``, ``/performance``, Copilot, insights) goes through
:func:`portfolio_report`.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import CashFlow, Portfolio, Transaction
from services.analytics.engine import FlowEvent, TradeEvent, reconstruct, summarize
from services.market_data.service import MarketDataService

DEFAULT_WINDOW_DAYS = 756
VALUE_CURVE_POINTS = 250


async def _ledger(
    session: AsyncSession, portfolio_id: int
) -> tuple[list[TradeEvent], list[FlowEvent]]:
    txs = (
        (await session.execute(select(Transaction).where(Transaction.portfolio_id == portfolio_id)))
        .scalars()
        .all()
    )
    rows = (
        (await session.execute(select(CashFlow).where(CashFlow.portfolio_id == portfolio_id)))
        .scalars()
        .all()
    )
    trades = [
        TradeEvent(
            when=t.executed_at.date(),
            symbol=t.ticker,
            side=t.side,
            quantity=float(t.quantity),
            gross=float(t.total_amount),
            costs=float(t.fees) + float(t.tax),
        )
        for t in txs
    ]
    flows = [
        FlowEvent(when=f.occurred_at.date(), kind=f.kind.upper(), amount=float(f.amount))
        for f in rows
    ]
    return trades, flows


async def value_history(
    session: AsyncSession,
    market: MarketDataService,
    portfolio: Portfolio,
    days: int = DEFAULT_WINDOW_DAYS,
) -> tuple[pd.Series, pd.Series]:
    """Daily portfolio value and **external** flow series (TL)."""
    trades, flows = await _ledger(session, portfolio.id)
    symbols = sorted(
        {t.symbol for t in trades}
        | {s for s, q in (portfolio.holdings or {}).items() if float(q) > 0}
    )
    panel = await market.history(symbols) if symbols else pd.DataFrame()
    if panel.empty:
        idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=2)
        value = float(portfolio.cash)
        return pd.Series([value, value], index=idx), pd.Series(0.0, index=idx)
    holdings = {s: float(q) for s, q in (portfolio.holdings or {}).items()}
    return reconstruct(panel.tail(days), holdings, float(portfolio.cash), trades, flows)


async def portfolio_report(
    session: AsyncSession,
    market: MarketDataService,
    portfolio: Portfolio,
    benchmark_symbol: str = "XU100.IS",
) -> dict[str, Any]:
    """Labelled TWR/MWR and tear sheet vs XU100, with the real TL risk-free rate."""
    values, flows = await value_history(session, market, portfolio)
    bench = await market.returns([benchmark_symbol])
    bench_series = bench[benchmark_symbol] if benchmark_symbol in bench else None
    funded_idx = values[values > max(1.0, float(values.abs().max()) * 1e-6)].index
    if len(funded_idx) > 1:  # Sharpe: dönemde geçerli faizlerin ortalaması (bugünkü değil)
        rf = market.mean_risk_free_rate(funded_idx[0], funded_idx[-1])
    else:
        rf = market.risk_free_rate()
    summary = summarize(values, flows, risk_free_rate=rf, benchmark=bench_series)
    # Fonlanmadan önceki (değeri ~0) günler eğride gösterilmez.
    funded = values[values > max(1.0, float(values.abs().max()) * 1e-6)]
    curve = values.loc[funded.index[0] :] if not funded.empty else values
    step = max(1, len(curve) // VALUE_CURVE_POINTS)
    return {
        "portfolio_id": portfolio.id,
        **summary,
        "benchmark_symbol": benchmark_symbol,
        "inflation_yoy": market.inflation_yoy(),
        "value_curve": [
            {"date": d.date().isoformat(), "value": round(float(v), 2)}
            for d, v in curve.iloc[::step].items()
        ],
    }


__all__ = ["DEFAULT_WINDOW_DAYS", "portfolio_report", "value_history"]
