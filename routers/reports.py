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


@router.post("/backtest", summary="Model portföy backtest (rebalance politikaları, maliyetli)")
async def backtest(
    request: Request, body: BacktestIn, session: SessionDep, user: UserDep
) -> dict[str, Any]:
    container = request.app.state.container
    level = body.level
    if body.customer_id is not None:
        customer: Customer = await load_customer_checked(session, user, body.customer_id)
        level = level or (await effective_level(session, customer)).level
    level = level or 5
    if body.weights:
        target = body.weights
    elif body.use_optimizer:
        res = await container.optimizer.optimize(
            OptimizationRequest(level=level, method=body.method)
        )
        target = res.weights
    else:
        target = {CLASS_REPRESENTATIVE[c]: w for c, w in get_policy().model_weights(level).items()}
    prices = await container.market.history(sorted(set(target) | {BENCHMARK_SYMBOL}))
    prices = prices.tail(int(body.years * 252))
    missing = [s for s in target if s not in prices.columns]
    if missing:
        raise HTTPException(status_code=422, detail=f"Fiyat verisi olmayan semboller: {missing}")
    bench = prices[BENCHMARK_SYMBOL].pct_change().dropna()
    kwargs: dict[str, Any] = {
        "initial": body.initial,
        "risk_free_rate": container.market.risk_free_rate(),
        "benchmark": bench,
    }
    results = (
        compare_policies(prices, target, **kwargs)
        if body.compare
        else {body.policy: run_backtest(prices, target, policy_name=body.policy, **kwargs)}
    )
    return {"level": level, "target": target, "results": results, "years": body.years}
