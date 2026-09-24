"""Backtest engine: a target allocation through history with a rebalancing
policy and transaction costs.

Policies:
    * ``none``     — buy and hold (weights drift freely),
    * ``calendar`` — back to target every ``calendar_days`` trading days,
    * ``band``     — back to target when any weight leaves its policy band.

Costs: each rebalance pays ``Σ |Δw| · V · cost_rate_i`` (commission, BSMV,
half-spread from the cost model). The engine is a simple, vectorised-per-day
loop so its behaviour is easy to test with synthetic series.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from core.policy import InvestmentPolicy, get_policy
from services.analytics.performance import tear_sheet
from services.market_data.universe import asset_class_of
from services.rebalancing.costs import CostModel

POLICIES = ("none", "calendar", "band")


def run_backtest(
    prices: pd.DataFrame,
    target: dict[str, float],
    *,
    policy_name: str = "band",
    initial: float = 100_000.0,
    calendar_days: int = 21,
    policy: InvestmentPolicy | None = None,
    risk_free_rate: float = 0.0,
    benchmark: pd.Series | None = None,
) -> dict[str, Any]:
    """Simulate ``target`` over ``prices`` (daily closes) and summarise."""
    if policy_name not in POLICIES:
        raise ValueError(f"Bilinmeyen politika: {policy_name}")
    policy = policy or get_policy()
    costs = CostModel(policy)
    cols = [s for s in target if s in prices.columns]
    px = prices[cols].dropna()
    w_t = np.array([target[s] for s in cols])
    w_t = w_t / w_t.sum()
    rates = np.array([costs.estimate(s, 10_000).total / 10_000 for s in cols])
    bands = np.array([policy.band_for(asset_class_of(s)) for s in cols])
    rets = px.pct_change().fillna(0.0).to_numpy()

    value = initial * (1.0 - float(rates @ w_t))  # ilk alım maliyeti
    w = w_t.copy()
    values = [value]
    n_rebal = 0
    total_cost = initial - value
    for t in range(1, rets.shape[0]):
        growth = w * (1.0 + rets[t])
        value = value * float(growth.sum())
        w = growth / growth.sum()
        due = (policy_name == "calendar" and t % calendar_days == 0) or (
            policy_name == "band" and bool(np.any(np.abs(w - w_t) > bands))
        )
        if due:
            cost = value * float(np.abs(w - w_t) @ rates)
            value -= cost
            total_cost += cost
            w = w_t.copy()
            n_rebal += 1
        values.append(value)
    series = pd.Series(values, index=px.index)
    daily = series.pct_change().dropna()
    stats = tear_sheet(daily, risk_free_rate=risk_free_rate, benchmark=benchmark)
    return {
        "policy": policy_name,
        "final_value": round(float(series.iloc[-1]), 2),
        "rebalances": n_rebal,
        "total_cost": round(float(total_cost), 2),
        "stats": stats,
        "equity_curve": [
            {"date": d.date().isoformat(), "value": round(float(v), 2)}
            for d, v in series.iloc[:: max(1, len(series) // 250)].items()
        ],
    }


def compare_policies(
    prices: pd.DataFrame, target: dict[str, float], **kwargs: Any
) -> dict[str, Any]:
    """Run all three policies on the same data."""
    return {p: run_backtest(prices, target, policy_name=p, **kwargs) for p in POLICIES}


__all__ = ["POLICIES", "compare_policies", "run_backtest"]
