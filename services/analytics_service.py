"""Portfolio analytics service — valuation, allocation and performance.

Provides read-only analytics that the API surfaces to advisors and customers:

    * **Valuation** — mark-to-market total value (cash + Σ qty × last_price),
      per-asset breakdown with current weights, and unrealized P&L vs a
      provided cost basis (defaults to the last known transaction price).
    * **Monte Carlo projection** — GBM wealth paths for planning views.

Performance metrics (TWR, MWR, Sharpe …) are *not* computed here: the single
ledger-based engine lives in :mod:`services.analytics.engine`.

All functions are pure (accept data in, return JSON-friendly dicts), so they
are unit-testable without the network or a database.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from core.logging import get_logger
from services.market_service import MarketSnapshot

logger = get_logger("otonom.analytics")

TRADING_DAYS = 252


@dataclass
class ValuationItem:
    """Mark-to-market facts for a single held ticker."""

    ticker: str
    quantity: float
    price: float
    market_value: float
    weight: float
    avg_cost: float | None = None
    unrealized_pnl: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "ticker": self.ticker,
            "quantity": self.quantity,
            "price": self.price,
            "market_value": round(self.market_value, 4),
            "weight": round(self.weight, 6),
            "avg_cost": round(self.avg_cost, 4) if self.avg_cost is not None else None,
            "unrealized_pnl": round(self.unrealized_pnl, 4)
            if self.unrealized_pnl is not None
            else None,
        }


@dataclass
class ValuationResult:
    """Overall valuation of a portfolio."""

    portfolio_id: int
    cash: float
    total_value: float
    invested_value: float
    items: list[ValuationItem] = field(default_factory=list)
    total_unrealized_pnl: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "portfolio_id": self.portfolio_id,
            "cash": round(self.cash, 4),
            "invested_value": round(self.invested_value, 4),
            "total_value": round(self.total_value, 4),
            "total_unrealized_pnl": round(self.total_unrealized_pnl, 4),
            "items": [item.to_dict() for item in self.items],
        }


class AnalyticsService:
    """Pure aggregation/computation over market snapshots and holdings."""

    # -- Valuation -----------------------------------------------------------

    def value_portfolio(
        self,
        portfolio_id: int,
        cash: float,
        holdings: dict[str, float],
        snapshots: dict[str, MarketSnapshot],
        cost_basis: dict[str, float] | None = None,
    ) -> ValuationResult:
        """Mark-to-market a portfolio.

        Args:
            portfolio_id: Target portfolio id (echoed back).
            cash: Available cash in the portfolio.
            holdings: ``{ticker: quantity}`` positions.
            snapshots: Market snapshots (used for latest prices).
            cost_basis: Optional ``{ticker: avg_cost}`` for P&L; when missing,
                no unrealized P&L is reported.

        Returns:
            A :class:`ValuationResult` with per-asset breakdown.
        """
        items: list[ValuationItem] = []
        invested = 0.0
        total_pnl = 0.0

        for ticker, qty in holdings.items():
            qty = float(qty)
            if qty <= 0:
                continue
            snap = snapshots.get(ticker)
            price = snap.last_price if snap else 0.0
            value = qty * price
            invested += value
            avg_cost = None
            pnl = None
            if cost_basis is not None and ticker in cost_basis:
                avg_cost = float(cost_basis[ticker])
                pnl = (price - avg_cost) * qty
                total_pnl += pnl
            weight = 0.0  # computed after total known
            items.append(
                ValuationItem(
                    ticker=ticker,
                    quantity=qty,
                    price=price,
                    market_value=value,
                    weight=weight,
                    avg_cost=avg_cost,
                    unrealized_pnl=pnl,
                )
            )

        total_value = cash + invested
        for item in items:
            item.weight = (item.market_value / total_value) if total_value > 0 else 0.0

        logger.info(
            "portfolio_valued",
            portfolio_id=portfolio_id,
            total_value=round(total_value, 2),
            items=len(items),
        )
        return ValuationResult(
            portfolio_id=portfolio_id,
            cash=cash,
            total_value=total_value,
            invested_value=invested,
            items=items,
            total_unrealized_pnl=total_pnl,
        )

    # -- Drift / rebalancing trigger bands ------------------------------------

    def drift_analysis(
        self,
        portfolio_id: int,
        cash: float,
        holdings: dict[str, float],
        snapshots: dict[str, MarketSnapshot],
        target_weights: dict[str, float],
        trigger_band: float = 0.05,
    ) -> dict[str, object]:
        """Compare actual allocation to target weights and flag rebalance need.

        A portfolio has drifted when any held asset's actual weight deviates
        from its target weight by more than ``trigger_band`` (absolute
        percentage points). Cash counts as the residual allocation. This drives
        band-based rebalancing: a run should only happen when at least one
        asset is outside its band, avoiding wasteful reorders.

        Args:
            portfolio_id: Target portfolio id (echoed back).
            cash: Available cash in the portfolio.
            holdings: ``{ticker: quantity}`` positions.
            snapshots: Market snapshots (latest prices).
            target_weights: ``{ticker: weight}`` desired allocation (fractions).
            trigger_band: Absolute drift (e.g. 0.05 = 5pp) that triggers.

        Returns:
            A JSON-friendly summary: per-asset drift rows, the max drift, whether
            rebalancing is needed, and the residual cash weight.
        """
        invested = 0.0
        values: dict[str, float] = {}
        for ticker, qty in holdings.items():
            qty = float(qty)
            if qty <= 0:
                continue
            snap = snapshots.get(ticker)
            price = snap.last_price if snap else 0.0
            value = qty * price
            values[ticker] = value
            invested += value
        total = cash + invested
        if total <= 0:
            return {
                "portfolio_id": portfolio_id,
                "total_value": 0.0,
                "assets": [],
                "max_drift": 0.0,
                "needs_rebalance": False,
                "cash_weight": 0.0,
                "note": "portföy boş",
            }

        all_syms = sorted(set(values) | set(target_weights) | set(holdings))
        rows: list[dict[str, object]] = []
        max_drift = 0.0
        for sym in all_syms:
            actual = (values.get(sym, 0.0) / total) if sym in values else 0.0
            target = float(target_weights.get(sym, 0.0) or 0.0)
            drift = actual - target
            max_drift = max(max_drift, abs(drift))
            rows.append(
                {
                    "ticker": sym,
                    "actual_weight": round(actual, 6),
                    "target_weight": round(target, 6),
                    "drift": round(drift, 6),
                    "outside_band": abs(drift) > trigger_band,
                    "market_value": round(values.get(sym, 0.0), 4),
                }
            )
        cash_weight = cash / total

        logger.info(
            "portfolio_drift",
            portfolio_id=portfolio_id,
            max_drift=round(max_drift, 4),
            needs_rebalance=max_drift > trigger_band,
            trigger_band=trigger_band,
        )
        return {
            "portfolio_id": portfolio_id,
            "total_value": round(total, 4),
            "assets": rows,
            "max_drift": round(max_drift, 6),
            "needs_rebalance": max_drift > trigger_band,
            "cash_weight": round(cash_weight, 6),
            "trigger_band": trigger_band,
        }


class MonteCarloProjection:
    """Stochastic portfolio projection via geometric Brownian motion.

    Simulates ``n_simulations`` independent value paths over a horizon using
    the portfolio's current value, expected annual return, and annualised
    volatility. Returns quantile summary (5th/50th/95th percentiles) for
    planning conversations (goal shortfall risk, retirement style projections)
    — the same family of output robo-advisors surface to customers.
    """

    def __init__(
        self,
        current_value: float,
        annual_return: float,
        annual_vol: float,
        horizon_years: float,
        n_simulations: int = 2000,
    ) -> None:
        self.current = float(current_value)
        self.mu = float(annual_return)
        self.sigma = float(annual_vol)
        self.horizon = float(horizon_years)
        self.n = int(n_simulations)

    def run(self, rng: np.random.Generator | None = None) -> dict[str, object]:
        """Simulate and return percentile endpoints of final wealth."""
        if self.current <= 0 or self.horizon <= 0:
            return {
                "current_value": self.current,
                "simulations": 0,
                "p5": self.current,
                "p50": self.current,
                "p95": self.current,
            }
        rng = rng or np.random.default_rng()
        steps = max(1, int(self.horizon * TRADING_DAYS))
        dt = self.horizon / steps
        # Paths: (sims, steps) compounding with GBM drift  mu - 0.5*sigma^2.
        shocks = rng.normal(0.0, 1.0, size=(self.n, steps))
        log_ret = (self.mu - 0.5 * self.sigma**2) * dt + self.sigma * np.sqrt(dt) * shocks
        final = self.current * np.exp(np.sum(log_ret, axis=1))
        p5, p50, p95 = np.percentile(final, [5, 50, 95])
        return {
            "current_value": round(self.current, 2),
            "horizon_years": self.horizon,
            "annual_return": round(self.mu, 6),
            "annual_volatility": round(self.sigma, 6),
            "simulations": self.n,
            "p5": round(float(p5), 2),
            "p50": round(float(p50), 2),
            "p95": round(float(p95), 2),
        }


__all__ = ["AnalyticsService", "MonteCarloProjection", "ValuationResult", "ValuationItem"]
