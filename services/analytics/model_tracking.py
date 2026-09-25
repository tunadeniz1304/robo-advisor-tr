"""Out-of-sample performance of the ten model portfolios.

For every risk level the default optimiser is run **walk-forward** (see
:mod:`services.analytics.walkforward`) on real prices: at each rebalance date
it only sees the estimation window, and the resulting weights are held until
the next date. The same test period is reported for the static model
portfolio (class representatives, band rebalancing) and the XU100 / TÜFE+3 /
60/40 benchmarks, so levels can be compared on identical dates.

``scripts/model_portfolio_report.py`` writes the result to
``data/snapshots/model_performance.json`` (served by
``GET /api/v1/model-portfolios/performance``) and ``docs/MODEL_PORTFOLIOS.md``.
"""

from __future__ import annotations

from typing import Any

from core.policy import InvestmentPolicy, get_policy
from services.analytics.backtest import run_backtest
from services.analytics.walkforward import walk_forward
from services.market_data.service import MarketDataService
from services.market_data.universe import CLASS_REPRESENTATIVE
from services.optimization.service import OptimizationRequest, OptimizationService

TRADING_DAYS = 252


def _stats(s: dict[str, Any]) -> dict[str, Any]:
    keys = ("cagr", "volatility", "sharpe", "max_drawdown", "total_return", "start", "end")
    return {k: s.get(k) for k in keys}


async def model_portfolio_performance(
    market: MarketDataService,
    *,
    levels: list[int] | None = None,
    years: float = 5.0,
    method: str | None = None,
    policy: InvestmentPolicy | None = None,
) -> dict[str, Any]:
    """Walk-forward and static performance of each level on the same test dates."""
    policy = policy or get_policy()
    cfg = policy.backtest
    optimizer = OptimizationService(market, policy)
    window = int(cfg["window_days"])
    bench_syms = [str(cfg["benchmark_equity"]), str(cfg["benchmark_bond"])]
    cpi = market.macro().get("TUFE")
    out: dict[str, Any] = {
        "method": method or policy.optimization.get("default_method"),
        "years": years,
        "levels": {},
    }
    bench_done = False
    for level in levels or list(range(1, 11)):
        req = OptimizationRequest(level=level, method=method)
        symbols = optimizer.allowed_symbols(req)
        panel = await market.history(sorted(set(symbols) | set(bench_syms)))
        panel = panel.tail(int(years * TRADING_DAYS) + window + 1)
        full = [s for s in symbols if s in panel.columns and panel[s].notna().all()]
        rf = market.mean_risk_free_rate(panel.index[window], panel.index[-1])

        def optimize(train: Any, as_of: Any, req: OptimizationRequest = req) -> dict[str, float]:
            return optimizer.optimize_on_returns(
                req, train, rf=market.risk_free_rate_at(as_of)
            ).weights

        wf = walk_forward(
            panel[full],
            optimize,
            risk_free_rate=rf,
            benchmark_prices=panel[[s for s in bench_syms if s in panel.columns]],
            cpi=cpi,
            policy=policy,
        )
        model = {CLASS_REPRESENTATIVE[c]: w for c, w in policy.model_weights(level).items()}
        test = panel.loc[wf["test_start"] :, list(model)]
        static = run_backtest(test, model, policy_name="band", risk_free_rate=rf, policy=policy)
        out["levels"][str(level)] = {
            "label": policy.risk_label(level),
            "test_start": wf["test_start"],
            "test_end": wf["test_end"],
            "walk_forward": {
                **_stats(wf["stats"]),
                "rebalances": wf["rebalances"],
                "total_cost": wf["total_cost"],
            },
            "static_model": {**_stats(static["stats"]), "rebalances": static["rebalances"]},
            "equity_curve": wf["equity_curve"],
            "excluded_short_history": sorted(set(symbols) - set(full)),
        }
        if not bench_done:
            out["benchmarks"] = {k: _stats(v["stats"]) for k, v in wf["benchmarks"].items()}
            out["benchmark_curves"] = {k: v["equity_curve"] for k, v in wf["benchmarks"].items()}
            out["risk_free_rate"] = rf
            bench_done = True
    return out


__all__ = ["model_portfolio_performance"]
