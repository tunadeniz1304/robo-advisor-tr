"""Portfolio value history reconstructed from the ledger.

Starting from today's positions, transactions are replayed **backwards** to
obtain daily quantities and cash; external flows come from ``cash_flows``.
The daily value series then feeds TWR, MWR (XIRR) and the tear sheet.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import CashFlow, Portfolio, Transaction
from services.analytics.performance import tear_sheet, time_weighted_return, xirr
from services.market_data.service import MarketDataService


async def value_history(
    session: AsyncSession, market: MarketDataService, portfolio: Portfolio, days: int = 756
) -> tuple[pd.Series, pd.Series]:
    """Daily portfolio value and external flow series (TL)."""
    txs = (
        (await session.execute(select(Transaction).where(Transaction.portfolio_id == portfolio.id)))
        .scalars()
        .all()
    )
    flows_rows = (
        (await session.execute(select(CashFlow).where(CashFlow.portfolio_id == portfolio.id)))
        .scalars()
        .all()
    )
    symbols = sorted(
        {t.ticker for t in txs} | {s for s, q in (portfolio.holdings or {}).items() if float(q) > 0}
    )
    panel = await market.history(symbols) if symbols else pd.DataFrame()
    if panel.empty:
        idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=2)
        v = float(portfolio.cash)
        return pd.Series([v, v], index=idx), pd.Series(0.0, index=idx)
    panel = panel.tail(days)
    idx = panel.index
    qty = {s: float((portfolio.holdings or {}).get(s, 0.0)) for s in symbols}
    cash = float(portfolio.cash)
    by_day: dict[pd.Timestamp, list[Any]] = defaultdict(list)
    for t in txs:
        by_day[pd.Timestamp(t.executed_at.date())].append(t)
    flow_by_day: dict[pd.Timestamp, float] = defaultdict(float)
    for fr in flows_rows:
        sign = 1.0 if fr.kind in {"DEPOSIT", "DIVIDEND"} else -1.0
        flow_by_day[pd.Timestamp(fr.occurred_at.date())] += sign * float(fr.amount)
    values: list[float] = []
    flows: list[float] = []
    for day in reversed(idx):
        row = panel.loc[day]
        values.append(
            cash + sum(qty[s] * float(row[s]) for s in symbols if qty[s] and row[s] == row[s])
        )
        # O günden sonraki işlemlere ait günün kapanışı: gün sonu değerinden geriye sar.
        f = sum(v for d, v in flow_by_day.items() if d == day)
        flows.append(f)
        for t in by_day.get(day, []):
            q, amt = float(t.quantity), float(t.total_amount)
            fees = float(t.fees) + float(t.tax)
            if t.side == "BUY":
                qty[t.ticker] = qty.get(t.ticker, 0.0) - q
                cash += amt + fees
            else:
                qty[t.ticker] = qty.get(t.ticker, 0.0) + q
                cash -= amt - fees
        cash -= f
    value_series = pd.Series(list(reversed(values)), index=idx)
    flow_series = pd.Series(list(reversed(flows)), index=idx)
    return value_series, flow_series


async def portfolio_report(
    session: AsyncSession,
    market: MarketDataService,
    portfolio: Portfolio,
    benchmark_symbol: str = "XU100.IS",
) -> dict[str, Any]:
    """TWR, MWR (XIRR) and tear sheet vs XU100 and TÜFE."""
    values, flows = await value_history(session, market, portfolio)
    twr, daily = time_weighted_return(values, flows)
    active = values[values > 0]
    cashflows: list[tuple[date, float]] = []
    if not active.empty:
        cashflows.append((active.index[0].date(), -float(active.iloc[0])))
        for d, f in flows.items():
            if f and d > active.index[0]:
                cashflows.append((d.date(), -float(f)))
        cashflows.append((active.index[-1].date(), float(active.iloc[-1])))
    bench = await market.returns([benchmark_symbol])
    bench_series = bench[benchmark_symbol] if benchmark_symbol in bench else None
    sheet = tear_sheet(daily, risk_free_rate=market.risk_free_rate(), benchmark=bench_series)
    return {
        "portfolio_id": portfolio.id,
        "twr": twr,
        "mwr": xirr(cashflows),
        "inflation_yoy": market.inflation_yoy(),
        "tear_sheet": sheet,
        "value_curve": [
            {"date": d.date().isoformat(), "value": round(float(v), 2)}
            for d, v in values.iloc[:: max(1, len(values) // 250)].items()
        ],
    }


__all__ = ["portfolio_report", "value_history"]
