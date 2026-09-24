"""Drift bands and the minimum-turnover rebalancing LP.

Given holdings, cash, prices and target weights:

* :func:`drift_report` — instrument and asset-class drift vs target with the
  policy bands (volatile classes get wider bands) and idle cash; the
  portfolio needs rebalancing only when something is outside its band.
* :func:`plan_trades` — solves (``scipy.optimize.linprog``)::

      min  Σ b_i + (1 + κ) Σ s_i + Σ k_i (b_i + s_i) + ε Σ d_i
      s.t. lo_i ≤ v_i + b_i − s_i ≤ hi_i            (instrument bands)
           lo_c ≤ Σ_{i∈c} (v_i + b_i − s_i) ≤ hi_c   (class bands)
           0 ≤ C − Σ (1+k_i) b_i + Σ (1−k_i) s_i ≤ c_max·V   (cash first)
           d_i ≥ |v_i + b_i − s_i − t_i V|,  0 ≤ s_i ≤ v_i

  ``b``/``s`` are TL buys/sells, ``k`` cost rates, ``κ`` the sell penalty
  (sales realise taxes) and ``ε`` a small pull toward the exact target so
  new cash first fills the most underweight positions. Trades below the
  minimum ticket are dropped. A portfolio already inside every band yields
  no orders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.optimize import linprog

from core.logging import get_logger
from core.policy import InvestmentPolicy, get_policy
from services.market_data.universe import asset_class_of
from services.rebalancing.costs import CostModel

logger = get_logger("otonom.rebalance.engine")

CASH = "NAKIT"
TARGET_PULL = 0.05
BAND_SAFETY = 0.9


@dataclass
class PortfolioState:
    """Mark-to-market snapshot of a portfolio."""

    cash: float
    quantities: dict[str, float]
    prices: dict[str, float]

    @property
    def values(self) -> dict[str, float]:
        return {
            s: float(q) * float(self.prices[s])
            for s, q in self.quantities.items()
            if float(q) > 0 and s in self.prices
        }

    @property
    def total(self) -> float:
        return float(self.cash) + sum(self.values.values())

    def weights(self) -> dict[str, float]:
        total = self.total
        if total <= 0:
            return {}
        out = {s: v / total for s, v in self.values.items()}
        out[CASH] = float(self.cash) / total
        return out


@dataclass
class TradePlan:
    """Result of the rebalancing optimisation."""

    needs_rebalance: bool
    trades: dict[str, float] = field(default_factory=dict)  # TL, + alış / − satış
    orders: list[dict[str, Any]] = field(default_factory=list)
    before_weights: dict[str, float] = field(default_factory=dict)
    after_weights: dict[str, float] = field(default_factory=dict)
    turnover: float = 0.0
    estimated_cost: float = 0.0
    cash_after: float = 0.0
    solver: str = "none"
    drift: dict[str, Any] = field(default_factory=dict)


def band_of(symbol: str, policy: InvestmentPolicy) -> float:
    return policy.band_for(asset_class_of(symbol))


def drift_report(
    state: PortfolioState, target: dict[str, float], policy: InvestmentPolicy | None = None
) -> dict[str, Any]:
    """Instrument/class drift vs target and the rebalancing decision."""
    policy = policy or get_policy()
    weights = state.weights()
    max_cash = float(policy.rebalance.get("max_cash_weight", 0.02))
    rows: list[dict[str, Any]] = []
    symbols = sorted((set(weights) - {CASH}) | set(target))
    outside = False
    for sym in symbols:
        actual = weights.get(sym, 0.0)
        tgt = float(target.get(sym, 0.0))
        band = band_of(sym, policy) if tgt > 0 else 0.0
        out = abs(actual - tgt) > band + 1e-9 and (tgt > 0 or actual > 1e-6)
        outside = outside or out
        rows.append(
            {
                "symbol": sym,
                "asset_class": asset_class_of(sym),
                "actual_weight": round(actual, 6),
                "target_weight": round(tgt, 6),
                "drift": round(actual - tgt, 6),
                "band": band,
                "outside_band": out,
                "market_value": round(state.values.get(sym, 0.0), 2),
            }
        )
    classes: dict[str, dict[str, float]] = {}
    for r in rows:
        c = classes.setdefault(r["asset_class"], {"actual": 0.0, "target": 0.0})
        c["actual"] += r["actual_weight"]
        c["target"] += r["target_weight"]
    class_rows = []
    for cls, v in sorted(classes.items()):
        band = policy.band_for(cls)
        out = abs(v["actual"] - v["target"]) > band + 1e-9
        outside = outside or out
        class_rows.append(
            {
                "asset_class": cls,
                "actual_weight": round(v["actual"], 6),
                "target_weight": round(v["target"], 6),
                "drift": round(v["actual"] - v["target"], 6),
                "band": band,
                "outside_band": out,
            }
        )
    cash_w = weights.get(CASH, 0.0)
    cash_out = cash_w > max_cash + 1e-9 and state.total > 0
    return {
        "total_value": round(state.total, 2),
        "assets": rows,
        "classes": class_rows,
        "cash_weight": round(cash_w, 6),
        "max_cash_weight": max_cash,
        "idle_cash": cash_out,
        "max_drift": round(max((abs(r["drift"]) for r in rows), default=0.0), 6),
        "needs_rebalance": bool(outside or cash_out),
    }


def plan_trades(
    state: PortfolioState,
    target: dict[str, float],
    *,
    policy: InvestmentPolicy | None = None,
    costs: CostModel | None = None,
    force: bool = False,
) -> TradePlan:
    """Minimum-turnover trades bringing every weight inside its band.

    Args:
        state: Current portfolio.
        target: Target weights (sum ≤ 1; the remainder is cash).
        policy: Investment policy (bands, penalties, minimum ticket).
        costs: Cost model (cost rates enter the LP).
        force: Plan even when everything is inside the bands.
    """
    policy = policy or get_policy()
    costs = costs or CostModel(policy)
    report = drift_report(state, target, policy)
    before = state.weights()
    if not report["needs_rebalance"] and not force:
        return TradePlan(
            needs_rebalance=False,
            before_weights=before,
            after_weights=before,
            cash_after=state.cash,
            drift=report,
        )

    total = state.total
    symbols = [s for s in sorted(set(target) | set(state.values)) if state.prices.get(s, 0) > 0]
    n = len(symbols)
    v0 = np.array([state.values.get(s, 0.0) for s in symbols])
    t = np.array([float(target.get(s, 0.0)) for s in symbols])
    band = np.array([band_of(s, policy) if target.get(s, 0) > 0 else 0.0 for s in symbols])
    k = np.array([costs.estimate(s, 10_000.0).total / 10_000.0 for s in symbols])
    # Maliyet/slipaj sonrası bant içinde kalmak için LP bandı %10 daraltılır.
    inner = band * BAND_SAFETY
    lo = np.maximum(t - inner, 0.0) * total
    hi = np.minimum(t + inner, 1.0) * total
    penalty = float(policy.rebalance.get("sell_penalty", 0.5))
    max_cash = float(policy.rebalance.get("max_cash_weight", 0.02))

    # değişkenler: b (n), s (n), d (n)
    c = np.concatenate([1.0 + k, 1.0 + penalty + k, np.full(n, TARGET_PULL)])
    eye = np.eye(n)
    rows: list[np.ndarray] = []
    rhs: list[float] = []
    # enstrüman bantları: v + b − s ≤ hi ; −(v + b − s) ≤ −lo
    for i in range(n):
        rows.append(np.concatenate([eye[i], -eye[i], np.zeros(n)]))
        rhs.append(hi[i] - v0[i])
        rows.append(np.concatenate([-eye[i], eye[i], np.zeros(n)]))
        rhs.append(v0[i] - lo[i])
    # sınıf bantları
    classes: dict[str, list[int]] = {}
    for i, s in enumerate(symbols):
        classes.setdefault(asset_class_of(s), []).append(i)
    for cls, idx in classes.items():
        tc = float(t[idx].sum())
        if tc <= 0:
            continue
        cband = policy.band_for(cls) * BAND_SAFETY
        mask = np.zeros(n)
        mask[idx] = 1.0
        rows.append(np.concatenate([mask, -mask, np.zeros(n)]))
        rhs.append(min(tc + cband, 1.0) * total - v0[idx].sum())
        rows.append(np.concatenate([-mask, mask, np.zeros(n)]))
        rhs.append(v0[idx].sum() - max(tc - cband, 0.0) * total)
    # nakit: C − Σ(1+k)b + Σ(1−k)s ≥ 0  ve  ≤ max_cash·V
    rows.append(np.concatenate([1.0 + k, -(1.0 - k), np.zeros(n)]))
    rhs.append(state.cash)
    rows.append(np.concatenate([-(1.0 + k), 1.0 - k, np.zeros(n)]))
    # Ücret/slipaj sonrası bant içinde kalmak için nakit tavanının yarısı hedeflenir.
    rhs.append(0.5 * max_cash * total - state.cash)
    # hedefe sapma: ±(v + b − s − tV) ≤ d
    for i in range(n):
        rows.append(np.concatenate([eye[i], -eye[i], -eye[i]]))
        rhs.append(t[i] * total - v0[i])
        rows.append(np.concatenate([-eye[i], eye[i], -eye[i]]))
        rhs.append(v0[i] - t[i] * total)
    bounds = [(0.0, None)] * n + [(0.0, float(v)) for v in v0] + [(0.0, None)] * n
    res = linprog(c, A_ub=np.vstack(rows), b_ub=np.array(rhs), bounds=bounds, method="highs")
    if res.success:
        buys, sells = res.x[:n], res.x[n : 2 * n]
        trades_arr = buys - sells
        solver = "min_turnover_lp"
    else:
        logger.warning("min_turnover_infeasible_exact_target", message=str(res.message))
        trades_arr = t * total * (1.0 - max_cash / 2) - v0
        solver = "exact_target_fallback"

    min_ticket = float(policy.rebalance.get("min_trade_amount", 0.0))
    trades: dict[str, float] = {}
    for s, amt in zip(symbols, trades_arr, strict=True):
        full_exit = amt < 0 and abs(amt) >= state.values.get(s, 0.0) - 1.0
        if abs(amt) >= max(min_ticket, 1.0) or (full_exit and abs(amt) > 0.01):
            trades[s] = float(amt)

    orders: list[dict[str, Any]] = []
    cash_after = float(state.cash)
    est_cost = 0.0
    after_values = dict(state.values)
    for s, amt in sorted(trades.items(), key=lambda kv: kv[1]):  # önce satışlar
        price = float(state.prices[s])
        qty = abs(amt) / price
        if amt < 0:
            qty = min(qty, float(state.quantities.get(s, 0.0)))
        qty = round(qty, 6)
        if qty <= 0:
            continue
        amount = qty * price
        cost = costs.estimate(s, amount)
        est_cost += cost.total
        side = "BUY" if amt > 0 else "SELL"
        cash_after += (
            -amount - cost.commission - cost.bsmv
            if side == "BUY"
            else amount - cost.commission - cost.bsmv
        )
        after_values[s] = after_values.get(s, 0.0) + (amount if side == "BUY" else -amount)
        orders.append(
            {
                "symbol": s,
                "ticker": s,
                "side": side,
                "quantity": qty,
                "price": price,
                "amount": round(amount, 2),
                "asset_class": asset_class_of(s),
                "cost": cost.to_dict(),
            }
        )
    after_total = cash_after + sum(v for v in after_values.values() if v > 0)
    after = (
        {s: v / after_total for s, v in after_values.items() if v > 1e-6} if after_total > 0 else {}
    )
    after[CASH] = cash_after / after_total if after_total > 0 else 0.0
    turnover = sum(o["amount"] for o in orders) / total if total > 0 else 0.0
    return TradePlan(
        needs_rebalance=True,
        trades=trades,
        orders=orders,
        before_weights=before,
        after_weights=after,
        turnover=turnover,
        estimated_cost=est_cost,
        cash_after=cash_after,
        solver=solver,
        drift=report,
    )


__all__ = ["CASH", "PortfolioState", "TradePlan", "band_of", "drift_report", "plan_trades"]
