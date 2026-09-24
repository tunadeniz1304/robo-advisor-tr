"""The single ledger-based performance engine.

Every performance number the API shows (``/report``, the deprecated
``/performance``, Copilot, insights) comes from here, so two endpoints can no
longer disagree about the same portfolio.

Pipeline:

1. :func:`reconstruct` — replays the ledger **backwards** from today's
   holdings and cash over a daily price panel. Every ledger event is mapped to
   a *valuation day* with :func:`valuation_day`: events on weekends/holidays
   roll forward to the next trading day's close, events after the last price
   day are valued on the last day, events before the window are already part
   of the opening state.
2. Cash-flow classification follows GIPS: only client **deposits and
   withdrawals** are external flows. Dividends, coupons, interest, fees,
   taxes and commissions change cash *inside* the portfolio and therefore
   belong to the time-weighted return.
3. :func:`summarize` — cumulative and annualised TWR, annualised MWR (XIRR)
   and the tear sheet, all computed against the same real TL risk-free rate
   and labelled with their period. Periods shorter than one year are not
   annualised (GIPS convention).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from services.analytics.performance import tear_sheet, time_weighted_return, xirr

EXTERNAL_FLOW_KINDS = frozenset({"DEPOSIT", "WITHDRAWAL"})
CASH_IN_KINDS = frozenset({"DEPOSIT", "DIVIDEND", "COUPON", "INTEREST"})
CASH_OUT_KINDS = frozenset({"WITHDRAWAL", "FEE", "TAX", "COMMISSION"})
FLOW_KINDS = CASH_IN_KINDS | CASH_OUT_KINDS
DAYS_PER_YEAR = 365.25
MIN_YEARS_TO_ANNUALIZE = 1.0


@dataclass(frozen=True)
class TradeEvent:
    """A buy or sell from the transaction ledger."""

    when: date
    symbol: str
    side: str  # BUY | SELL
    quantity: float
    gross: float  # quantity × price
    costs: float  # commission + BSMV + withholding tax


@dataclass(frozen=True)
class FlowEvent:
    """A cash movement from ``cash_flows`` (``amount`` is a positive magnitude)."""

    when: date
    kind: str
    amount: float

    @property
    def cash_delta(self) -> float:
        """Signed change of portfolio cash."""
        if self.kind in CASH_IN_KINDS:
            return abs(self.amount)
        if self.kind in CASH_OUT_KINDS:
            return -abs(self.amount)
        raise ValueError(f"Bilinmeyen nakit akışı türü: {self.kind}")

    @property
    def is_external(self) -> bool:
        """GIPS: only client contributions/withdrawals are external flows."""
        return self.kind in EXTERNAL_FLOW_KINDS


def valuation_day(when: date, index: pd.DatetimeIndex) -> pd.Timestamp | None:
    """Trading day whose close first reflects an event dated ``when``.

    Returns ``None`` for events before the first day of the window (they are
    already contained in the opening state).
    """
    ts = pd.Timestamp(when).normalize()
    if len(index) == 0 or ts < index[0]:
        return None
    pos = int(index.searchsorted(ts, side="left"))
    return index[min(pos, len(index) - 1)]


def reconstruct(
    prices: pd.DataFrame,
    holdings: dict[str, float],
    cash: float,
    trades: Iterable[TradeEvent],
    flows: Iterable[FlowEvent],
) -> tuple[pd.Series, pd.Series]:
    """Daily portfolio value and **external** flow series.

    Args:
        prices: Daily close panel (index = valuation days, columns = symbols).
        holdings: Current quantities ``{symbol: qty}``.
        cash: Current cash.
        trades: Ledger trades.
        flows: Ledger cash flows (all kinds).

    Returns:
        ``(values, external_flows)`` on ``prices.index``; a flow on day *t*
        is already contained in the value at the close of *t*.
    """
    index = pd.DatetimeIndex(prices.index)
    qty = defaultdict(float, {s: float(q) for s, q in holdings.items()})
    cash_now = float(cash)
    trades_by_day: dict[pd.Timestamp, list[TradeEvent]] = defaultdict(list)
    for t in trades:
        day = valuation_day(t.when, index)
        if day is not None:
            trades_by_day[day].append(t)
    cash_by_day: dict[pd.Timestamp, float] = defaultdict(float)
    external_by_day: dict[pd.Timestamp, float] = defaultdict(float)
    for f in flows:
        day = valuation_day(f.when, index)
        if day is None:
            continue
        cash_by_day[day] += f.cash_delta
        if f.is_external:
            external_by_day[day] += f.cash_delta

    values: list[float] = []
    externals: list[float] = []
    for day in reversed(index):
        row = prices.loc[day]
        invested = sum(
            q * float(row[s]) for s, q in qty.items() if q and s in row.index and pd.notna(row[s])
        )
        values.append(cash_now + invested)
        externals.append(external_by_day.get(day, 0.0))
        # Günün olaylarını geri sar → bir önceki günün kapanış durumu.
        for t in trades_by_day.get(day, []):
            if t.side == "BUY":
                qty[t.symbol] -= t.quantity
                cash_now += t.gross + t.costs
            else:
                qty[t.symbol] += t.quantity
                cash_now -= t.gross - t.costs
        cash_now -= cash_by_day.get(day, 0.0)
    return (
        pd.Series(list(reversed(values)), index=index, dtype=float),
        pd.Series(list(reversed(externals)), index=index, dtype=float),
    )


def _period(start: pd.Timestamp, end: pd.Timestamp) -> dict[str, Any]:
    days = int((end - start).days)
    years = days / DAYS_PER_YEAR
    return {
        "start": start.date().isoformat(),
        "end": end.date().isoformat(),
        "days": days,
        "years": round(years, 4),
        "annualized": years >= MIN_YEARS_TO_ANNUALIZE,
    }


def summarize(
    values: pd.Series,
    external_flows: pd.Series,
    *,
    risk_free_rate: float,
    benchmark: pd.Series | None = None,
) -> dict[str, Any]:
    """Labelled performance metrics of a reconstructed value series.

    ``twr_cumulative`` is the chain-linked return of the whole period;
    ``twr_annualized`` and ``mwr_annualized`` are ``None`` for periods under
    one year. The tear sheet uses the same daily TWR returns and the same
    risk-free rate.
    """
    floor = max(1.0, float(values.abs().max()) * 1e-6) if len(values) else 1.0
    funded = values[values > floor]
    if funded.shape[0] < 2:
        empty_day = values.index[-1] if len(values) else pd.Timestamp.today().normalize()
        return {
            "twr_cumulative": 0.0,
            "twr_annualized": None,
            "mwr_annualized": None,
            "period": _period(empty_day, empty_day),
            "tear_sheet": {"observations": 0, "risk_free_rate": risk_free_rate},
        }
    start, end = funded.index[0], funded.index[-1]
    window = values.loc[start:end]
    flows = external_flows.reindex(window.index).fillna(0.0)
    twr, daily = time_weighted_return(window, flows)
    period = _period(start, end)
    years = period["years"]
    twr_ann = (1.0 + twr) ** (1.0 / years) - 1.0 if period["annualized"] and twr > -1 else None

    cashflows: list[tuple[date, float]] = [(start.date(), -float(window.iloc[0]))]
    for day, amount in flows.iloc[1:].items():
        if amount:
            cashflows.append((pd.Timestamp(day).date(), -float(amount)))
    cashflows.append((end.date(), float(window.iloc[-1])))
    mwr = xirr(cashflows) if period["annualized"] else None

    sheet = tear_sheet(daily, risk_free_rate=risk_free_rate, benchmark=benchmark, years=years)
    return {
        "twr_cumulative": twr,
        "twr_annualized": twr_ann,
        "mwr_annualized": mwr,
        "period": period,
        "tear_sheet": sheet,
    }


__all__ = [
    "CASH_IN_KINDS",
    "CASH_OUT_KINDS",
    "EXTERNAL_FLOW_KINDS",
    "FLOW_KINDS",
    "FlowEvent",
    "TradeEvent",
    "reconstruct",
    "summarize",
    "valuation_day",
]
