"""Analytics router — valuation, drift, projection and performance.

All endpoints use the **shared** market service of the app container, so the
TTL cache actually works across requests (bug #7).
"""

from __future__ import annotations

from typing import Annotated, Any

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.deps import SessionDep, UserDep, load_portfolio_checked
from models import AdvisorRun, Portfolio
from services.analytics_service import AnalyticsService, MonteCarloProjection
from services.market_service import MarketService, MarketSnapshot
from services.portfolio_service import PortfolioService
from services.tax_lots import average_cost_basis

router = APIRouter(prefix="/portfolios", tags=["portfolios"])


class ValuationItemOut(BaseModel):
    ticker: str
    quantity: float
    price: float
    market_value: float
    weight: float
    avg_cost: float | None = None
    unrealized_pnl: float | None = None


class ValuationOut(BaseModel):
    portfolio_id: int
    cash: float
    invested_value: float
    total_value: float
    total_unrealized_pnl: float
    items: list[ValuationItemOut]


class PerformanceOut(BaseModel):
    portfolio_id: int
    total_return: float
    annualized_return: float
    volatility: float
    sharpe_ratio: float
    max_drawdown: float
    var_95: float
    observations: int


def market_of(request: Request) -> MarketService:
    """Shared market service of the application container."""
    return request.app.state.container.market  # type: ignore[no-any-return]


def _held(portfolio: Portfolio) -> list[str]:
    return [t for t, q in (portfolio.holdings or {}).items() if float(q) > 0]


async def _snapshots(request: Request, portfolio: Portfolio) -> dict[str, MarketSnapshot]:
    tickers = _held(portfolio)
    return await market_of(request).fetch_snapshots(tickers) if tickers else {}


def _market_weights(
    portfolio: Portfolio, snapshots: dict[str, MarketSnapshot]
) -> tuple[dict[str, float], float, float]:
    """Return ``(weights, cash_weight, total_value)`` by market value."""
    values = {
        t: float(q) * snapshots[t].last_price
        for t, q in (portfolio.holdings or {}).items()
        if t in snapshots and float(q) > 0
    }
    cash = float(portfolio.cash)
    total = cash + sum(values.values())
    if total <= 0:
        return {}, 0.0, 0.0
    return {t: v / total for t, v in values.items()}, cash / total, total


async def latest_target_weights(
    session: AsyncSession, portfolio_id: int
) -> dict[str, float] | None:
    """Target weights of the most recent advisor run for the portfolio."""
    run = (
        await session.execute(
            select(AdvisorRun)
            .where(AdvisorRun.portfolio_id == portfolio_id)
            .order_by(AdvisorRun.created_at.desc(), AdvisorRun.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if run is None or not run.target_weights:
        return None
    return {str(k): float(v) for k, v in run.target_weights.items()}


@router.get("/{portfolio_id}/valuation", response_model=ValuationOut, summary="Güncel değerleme")
async def get_valuation(
    request: Request, portfolio_id: int, session: SessionDep, user: UserDep
) -> ValuationOut:
    """Mark-to-market valuation with lot-based average cost and P&L."""
    portfolio = await load_portfolio_checked(session, user, portfolio_id)
    snapshots = await _snapshots(request, portfolio)
    basis = await average_cost_basis(session, portfolio_id)
    result = AnalyticsService().value_portfolio(
        portfolio_id=portfolio.id,
        cash=float(portfolio.cash),
        holdings=dict(portfolio.holdings or {}),
        snapshots=snapshots,
        cost_basis=basis,
    )
    return ValuationOut.model_validate(result.to_dict())


@router.get("/{portfolio_id}/drift", summary="Hedef dağılıma göre sapma (bant analizi)")
async def get_drift(
    request: Request,
    portfolio_id: int,
    session: SessionDep,
    user: UserDep,
    trigger_band: Annotated[float, Query(ge=0.0, le=0.5)] = 0.03,
) -> dict[str, Any]:
    """Drift vs the portfolio's current target allocation (bug #4: no stub).

    The target is the latest approved/proposed allocation of the portfolio.
    Without any target the endpoint answers 409 with a clear next step.
    """
    portfolio = await load_portfolio_checked(session, user, portfolio_id)
    target = await latest_target_weights(session, portfolio_id)
    if not target:
        raise HTTPException(
            status_code=409,
            detail="Bu portföy için hedef dağılım yok. Önce bir yeniden dengeleme önerisi oluşturun.",
        )
    tickers = sorted(set(_held(portfolio)) | set(target))
    snapshots = await market_of(request).fetch_snapshots(tickers) if tickers else {}
    return AnalyticsService().drift_analysis(
        portfolio_id=portfolio.id,
        cash=float(portfolio.cash),
        holdings=dict(portfolio.holdings or {}),
        snapshots=snapshots,
        target_weights=target,
        trigger_band=trigger_band,
    )


@router.get("/{portfolio_id}/projection", summary="Monte Carlo projeksiyon (GBM)")
async def get_projection(
    request: Request,
    portfolio_id: int,
    session: SessionDep,
    user: UserDep,
    horizon_years: Annotated[float, Query(ge=0.1, le=50)] = 10.0,
    n_simulations: Annotated[int, Query(ge=100, le=10000)] = 2000,
    risk_free_rate: Annotated[float, Query(ge=0.0, le=1.0)] = 0.0,
) -> dict[str, Any]:
    """Wealth projection with statistically estimated drift and volatility.

    Expected return = market-value weighted, James-Stein shrunk historical
    mean (≥3y data) plus cash at ``risk_free_rate``; volatility from the
    covariance matrix (bug #5: no more 1-month momentum/12 hack).
    """
    portfolio = await load_portfolio_checked(session, user, portfolio_id)
    snapshots = await _snapshots(request, portfolio)
    weights, cash_w, total = _market_weights(portfolio, snapshots)
    expected, vol = risk_free_rate * cash_w, 0.0
    returns = market_of(request).build_returns_frame(snapshots)
    cols = [c for c in returns.columns if c in weights]
    if cols and returns.shape[0] >= 2:
        frame = returns[cols].dropna()
        svc = PortfolioService()
        mu = svc.expected_returns(frame).to_numpy(dtype=float)
        cov = svc.covariance_matrix(frame).to_numpy(dtype=float)
        w = np.array([weights[c] for c in cols])
        expected += float(w @ mu)
        vol = float(np.sqrt(max(w @ cov @ w, 0.0)))
    proj = MonteCarloProjection(
        current_value=total,
        annual_return=expected,
        annual_vol=vol,
        horizon_years=horizon_years,
        n_simulations=n_simulations,
    )
    return proj.run(rng=np.random.default_rng(portfolio_id))


@router.get(
    "/{portfolio_id}/performance",
    response_model=PerformanceOut,
    summary="Performans metrikleri (bileşik getiri, Sharpe, VaR, maks. düşüş)",
)
async def get_performance(
    request: Request,
    portfolio_id: int,
    session: SessionDep,
    user: UserDep,
    risk_free_rate: Annotated[float, Query(ge=0.0, le=1.0)] = 0.0,
) -> PerformanceOut:
    """Metrics of the current portfolio (actual market-value weights)."""
    portfolio = await load_portfolio_checked(session, user, portfolio_id)
    snapshots = await _snapshots(request, portfolio)
    weights, cash_w, _ = _market_weights(portfolio, snapshots)
    returns = market_of(request).build_returns_frame(snapshots)
    metrics = AnalyticsService().performance_metrics(
        returns, risk_free_rate=risk_free_rate, weights=weights or None, cash_weight=cash_w
    )
    return PerformanceOut.model_validate({"portfolio_id": portfolio_id, **metrics})
