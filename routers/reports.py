"""Analytics reports: portfolio tear sheet (TWR/MWR) and backtests."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.deps import SessionDep, UserDep, load_customer_checked, load_portfolio_checked
from core.policy import get_policy
from models import Customer
from services.analytics.backtest import POLICIES, compare_policies, run_backtest
from services.analytics.history import portfolio_report
from services.analytics.walkforward import benchmark_report, walk_forward
from services.market_data.universe import BENCHMARK_SYMBOL, CLASS_REPRESENTATIVE
from services.optimization.service import OptimizationRequest
from services.suitability.service import effective_level

router = APIRouter(tags=["analytics"])


class BacktestIn(BaseModel):
    customer_id: int | None = Field(default=None, description="Seviye bu müşteriden alınır")
    level: int | None = Field(default=None, ge=1, le=10)
    weights: dict[str, float] | None = Field(default=None, description="Doğrudan hedef ağırlıklar")
    use_optimizer: bool = Field(default=False, description="Hedefi optimizasyonla üret")
    method: str | None = None
    policy: str = Field(default="band", pattern="^(" + "|".join(POLICIES) + ")$")
    compare: bool = True
    years: float = Field(default=5, gt=0.5, le=10)
    initial: float = Field(default=100_000, gt=0)


@router.get("/portfolios/{portfolio_id}/report", summary="Tear sheet, TWR ve MWR")
async def report(
    request: Request, portfolio_id: int, session: SessionDep, user: UserDep
) -> dict[str, Any]:
    portfolio = await load_portfolio_checked(session, user, portfolio_id)
    return await portfolio_report(session, request.app.state.container.market, portfolio)


@router.post(
    "/backtest", summary="Backtest: sabit hedef (politika karşılaştırması) veya walk-forward"
)
async def backtest(
    request: Request, body: BacktestIn, session: SessionDep, user: UserDep
) -> dict[str, Any]:
    """Backtest a target allocation or an optimiser.

    * ``use_optimizer=true`` → **walk-forward**: at every rebalance date the
      optimiser sees only the estimation window ending on that date and the
      policy rate in force then (no look-ahead).
    * otherwise the fixed target (model portfolio or given weights) is run
      under the three rebalancing policies.

    Both modes report XU100, TÜFE+3 and 60/40 benchmarks on the same dates.
    """
    container = request.app.state.container
    policy = get_policy()
    cfg = policy.backtest
    level = body.level
    if body.customer_id is not None:
        customer: Customer = await load_customer_checked(session, user, body.customer_id)
        level = level or (await effective_level(session, customer)).level
    level = level or 5
    market = container.market
    bench_syms = [str(cfg["benchmark_equity"]), str(cfg["benchmark_bond"])]
    cpi = market.macro().get("TUFE")
    test_days = int(body.years * 252)

    if body.use_optimizer and not body.weights:
        req = OptimizationRequest(level=level, method=body.method)
        symbols = container.optimizer.allowed_symbols(req)
        window = int(cfg["window_days"])
        panel = await market.history(sorted(set(symbols) | set(bench_syms)))
        panel = panel.tail(test_days + window + 1)
        full = [s for s in symbols if s in panel.columns and panel[s].notna().all()]
        short = sorted(set(symbols) - set(full))
        if len(panel) <= window + 1 or not full:
            raise HTTPException(
                status_code=422, detail="Walk-forward için yeterli fiyat geçmişi yok."
            )

        def optimizer(train: Any, as_of: Any) -> dict[str, float]:
            res = container.optimizer.optimize_on_returns(
                req, train, rf=market.risk_free_rate_at(as_of)
            )
            return res.weights

        rf_test = market.mean_risk_free_rate(panel.index[window], panel.index[-1])
        try:
            result = walk_forward(
                panel[full],
                optimizer,
                initial=body.initial,
                risk_free_rate=rf_test,
                benchmark_prices=panel[[s for s in bench_syms if s in panel.columns]],
                cpi=cpi,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "level": level,
            "method": req.method or policy.optimization.get("default_method"),
            "years": body.years,
            "excluded_short_history": short,
            "risk_free_rate": rf_test,
            **result,
        }

    if body.weights:
        target = body.weights
    else:
        target = {CLASS_REPRESENTATIVE[c]: w for c, w in policy.model_weights(level).items()}
    prices = await market.history(sorted(set(target) | set(bench_syms) | {BENCHMARK_SYMBOL}))
    prices = prices.tail(test_days)
    missing = [s for s in target if s not in prices.columns]
    if missing:
        raise HTTPException(status_code=422, detail=f"Fiyat verisi olmayan semboller: {missing}")
    bench = prices[BENCHMARK_SYMBOL].pct_change().dropna()
    rf = market.mean_risk_free_rate(prices.index[0], prices.index[-1])
    kwargs: dict[str, Any] = {"initial": body.initial, "risk_free_rate": rf, "benchmark": bench}
    results = (
        compare_policies(prices, target, **kwargs)
        if body.compare
        else {body.policy: run_backtest(prices, target, policy_name=body.policy, **kwargs)}
    )
    px = prices[list(target)].dropna()
    return {
        "mode": "fixed_weights",
        "level": level,
        "target": target,
        "results": results,
        "years": body.years,
        "risk_free_rate": rf,
        "benchmarks": benchmark_report(px.index, prices, cpi, body.initial, rf),
    }
