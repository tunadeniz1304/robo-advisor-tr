"""Walk-forward backtest: out-of-sample evaluation of an optimiser.

At every rebalance date *t* the optimiser receives **only** the returns in
the estimation window ending at *t* (rolling ``window_days`` or expanding)
and its weights are held — drifting with prices — until the next rebalance.
Changing prices after *t* can therefore never change the weights chosen at
*t* (tested). The first ``window_days`` observations are estimation-only;
performance is measured on the remaining test period, together with the
benchmarks on exactly the same dates:

* ``XU100``  — buy and hold of the equity index,
* ``TUFE+3`` — CPI plus a real spread (inflation-protection target),
* ``60/40``  — 60 % equity index / 40 % TL bond fund, rebalanced monthly.

All parameters (window, frequency, spread, mix, symbols) come from the
``[backtest]`` section of the policy file.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd

from core.policy import InvestmentPolicy, get_policy
from services.analytics.performance import tear_sheet
from services.rebalancing.costs import CostModel

Optimizer = Callable[[pd.DataFrame, pd.Timestamp], dict[str, float]]
CURVE_POINTS = 250


def _curve(series: pd.Series) -> list[dict[str, Any]]:
    step = max(1, len(series) // CURVE_POINTS)
    return [
        {"date": d.date().isoformat(), "value": round(float(v), 2)}
        for d, v in series.iloc[::step].items()
    ]


def _normalise(weights: dict[str, float], cols: list[str]) -> np.ndarray:
    w = np.array([max(float(weights.get(c, 0.0)), 0.0) for c in cols])
    total = float(w.sum())
    if total <= 0:
        raise ValueError("Optimizer boş ağırlık döndürdü.")
    return w / total


def _cpi_target_index(cpi: pd.Series, index: pd.DatetimeIndex, spread: float) -> pd.Series:
    """Daily TÜFE + spread index on ``index`` (log-linear between month ends)."""
    monthly = cpi.dropna().sort_index()
    grid = monthly.index.union(index)
    log_cpi = np.log(monthly.reindex(grid)).interpolate(method="time").ffill().bfill()
    daily = np.exp(log_cpi.reindex(index))
    years = (index - index[0]).days.to_numpy() / 365.25
    return pd.Series(daily.to_numpy() * (1.0 + spread) ** years, index=index)


def _benchmarks(
    index: pd.DatetimeIndex,
    benchmark_prices: pd.DataFrame | None,
    cpi: pd.Series | None,
    cfg: dict[str, Any],
    initial: float,
    risk_free_rate: float,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    equity, bond = str(cfg["benchmark_equity"]), str(cfg["benchmark_bond"])
    series: dict[str, pd.Series] = {}
    if benchmark_prices is not None and equity in benchmark_prices.columns:
        px = benchmark_prices.reindex(index).ffill()
        eq = px[equity] / px[equity].iloc[0]
        series["XU100"] = eq
        if bond in px.columns:
            mix = float(cfg["mix_equity"])
            rets = px[[equity, bond]].pct_change().fillna(0.0)
            month = index.to_period("M")
            w = np.array([mix, 1.0 - mix])
            value, values = 1.0, [1.0]
            for t in range(1, len(index)):
                growth = w * (1.0 + rets.iloc[t].to_numpy())
                value *= float(growth.sum())
                w = growth / growth.sum()
                if month[t] != month[t - 1]:  # ay başında sabit karışıma dön
                    w = np.array([mix, 1.0 - mix])
                values.append(value)
            series["60/40"] = pd.Series(values, index=index)
    if cpi is not None and not cpi.dropna().empty:
        target = _cpi_target_index(cpi, index, float(cfg["cpi_spread"]))
        series["TUFE+3"] = target / target.iloc[0]
    for name, s in series.items():
        daily = s.pct_change().iloc[1:]
        out[name] = {
            "final_value": round(float(initial * s.iloc[-1]), 2),
            "stats": tear_sheet(daily, risk_free_rate=risk_free_rate),
            "equity_curve": _curve(initial * s),
        }
    return out


def benchmark_report(
    index: pd.DatetimeIndex,
    prices: pd.DataFrame,
    cpi: pd.Series | None,
    initial: float,
    risk_free_rate: float,
    policy: InvestmentPolicy | None = None,
) -> dict[str, Any]:
    """XU100, TÜFE+spread and 60/40 benchmarks on ``index``."""
    cfg = (policy or get_policy()).backtest
    return _benchmarks(pd.DatetimeIndex(index), prices, cpi, cfg, initial, risk_free_rate)


def walk_forward(
    prices: pd.DataFrame,
    optimizer: Optimizer,
    *,
    window_days: int | None = None,
    rebalance_days: int | None = None,
    expanding: bool = False,
    initial: float = 100_000.0,
    risk_free_rate: float = 0.0,
    benchmark_prices: pd.DataFrame | None = None,
    cpi: pd.Series | None = None,
    policy: InvestmentPolicy | None = None,
) -> dict[str, Any]:
    """Run a walk-forward backtest of ``optimizer`` over ``prices``.

    Args:
        prices: Daily closes (index = trading days, columns = instruments).
        optimizer: ``f(returns_window, as_of) -> weights``; the window ends at
            ``as_of`` (inclusive) and contains nothing later.
        window_days: Estimation window length in trading days.
        rebalance_days: Trading days between re-optimisations.
        expanding: Use all data since the start instead of a rolling window.
        initial: Initial portfolio value (TL).
        risk_free_rate: Annual rate for the test-period Sharpe/Sortino.
        benchmark_prices: Closes of the benchmark equity index and bond fund.
        cpi: Monthly CPI index for the TÜFE + spread benchmark.
        policy: Investment policy (costs, defaults).

    Returns:
        Stats, equity curve, weights history and benchmarks of the test period.
    """
    policy = policy or get_policy()
    cfg = policy.backtest
    window = int(window_days if window_days is not None else cfg["window_days"])
    step = int(rebalance_days if rebalance_days is not None else cfg["rebalance_days"])
    if window <= 0 or step <= 0:
        raise ValueError("Tahmin penceresi ve rebalance aralığı pozitif olmalı.")
    px = prices.sort_index().ffill().dropna(how="any")
    if px.shape[0] <= window + 1:
        raise ValueError(
            f"Walk-forward için yeterli veri yok: {px.shape[0]} gün < pencere {window} + test."
        )
    cols = list(px.columns)
    rets = px.pct_change()
    costs = CostModel(policy)
    rates = np.array([costs.estimate(s, 10_000.0).total / 10_000.0 for s in cols])

    start = window
    value = float(initial)
    w = np.zeros(len(cols))
    values: list[float] = []
    history: list[dict[str, Any]] = []
    total_cost = 0.0
    for t in range(start, px.shape[0]):
        if t > start:
            growth = w * (1.0 + rets.iloc[t].to_numpy())
            value *= float(growth.sum())
            w = growth / growth.sum()
        if (t - start) % step == 0:
            as_of = px.index[t]
            lo = 1 if expanding else max(1, t - window + 1)
            train = rets.iloc[lo : t + 1]
            target = _normalise(optimizer(train, as_of), cols)
            cost = value * float(np.abs(target - w) @ rates)
            value -= cost
            total_cost += cost
            w = target
            history.append(
                {
                    "date": as_of.date().isoformat(),
                    "weights": {
                        c: round(float(x), 6) for c, x in zip(cols, w, strict=True) if x > 1e-6
                    },
                }
            )
        values.append(value)
    test_index = px.index[start:]
    equity = pd.Series(values, index=test_index)
    daily = equity.pct_change().iloc[1:]
    stats = tear_sheet(daily, risk_free_rate=risk_free_rate)
    return {
        "mode": "walk_forward",
        "window_days": window,
        "rebalance_days": step,
        "window": "expanding" if expanding else "rolling",
        "data_start": px.index[0].date().isoformat(),
        "test_start": test_index[0].date().isoformat(),
        "test_end": test_index[-1].date().isoformat(),
        "final_value": round(float(equity.iloc[-1]), 2),
        "rebalances": len(history),
        "total_cost": round(total_cost, 2),
        "stats": stats,
        "equity_curve": _curve(equity),
        "weights_history": history,
        "benchmarks": _benchmarks(test_index, benchmark_prices, cpi, cfg, initial, risk_free_rate),
    }


__all__ = ["Optimizer", "benchmark_report", "walk_forward"]
