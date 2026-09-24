"""Analytics router — valuation, drift, projection and performance.

All endpoints use the **shared** market service of the app container, so the
TTL cache actually works across requests (bug #7).
"""

from __future__ import annotations

from typing import Annotated, Any

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.deps import SessionDep, UserDep, load_portfolio_checked
from models import AdvisorRun, Portfolio, RebalanceProposal
from services.analytics.history import portfolio_report
from services.analytics_service import AnalyticsService, MonteCarloProjection
from services.market_data.service import MarketDataService
from services.market_service import MarketSnapshot
from services.portfolio_service import PortfolioService
from services.rebalancing.costs import harvest_candidates
from services.rebalancing.engine import PortfolioState, drift_report
from services.tax_lots import average_cost_basis, open_lots

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
    """Summary of the ledger engine (same numbers as ``/report``)."""

    portfolio_id: int
    twr_cumulative: float
    twr_annualized: float | None
    mwr_annualized: float | None
    volatility: float | None
    sharpe: float | None
    sortino: float | None
    max_drawdown: float | None
    var_95_hist: float | None
    observations: int
    risk_free_rate: float
    period: dict[str, Any]
    deprecated: bool = True
    successor: str


def market_of(request: Request) -> MarketDataService:
    """Shared market data service of the application container."""
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
    """Target weights of the latest proposal (or the legacy advisor run)."""
    proposal = (
        await session.execute(
            select(RebalanceProposal)
            .where(RebalanceProposal.portfolio_id == portfolio_id)
            .order_by(RebalanceProposal.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if proposal is not None and proposal.target_weights:
        return {str(k): float(v) for k, v in proposal.target_weights.items()}
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
    request: Request, portfolio_id: int, session: SessionDep, user: UserDep
) -> dict[str, Any]:
    """Drift vs the portfolio's current target allocation (bug #4: no stub).

    The target is the latest proposal's allocation; bands come from the
    investment policy (wider for volatile classes). Without any target the
    endpoint answers 409 with a clear next step.
    """
    portfolio = await load_portfolio_checked(session, user, portfolio_id)
    target = await latest_target_weights(session, portfolio_id)
    if not target:
        raise HTTPException(
            status_code=409,
            detail="Bu portföy için hedef dağılım yok. Önce bir yeniden dengeleme önerisi oluşturun.",
        )
    tickers = sorted(set(_held(portfolio)) | set(target))
    prices = await request.app.state.container.proposals.current_prices(tickers)
    state = PortfolioState(
        cash=float(portfolio.cash),
        quantities={k: float(v) for k, v in (portfolio.holdings or {}).items()},
        prices=prices,
    )
    return {"portfolio_id": portfolio.id, **drift_report(state, target)}


@router.get("/{portfolio_id}/projection", summary="Monte Carlo projeksiyon (GBM)")
async def get_projection(
    request: Request,
    portfolio_id: int,
    session: SessionDep,
    user: UserDep,
    horizon_years: Annotated[float, Query(ge=0.1, le=50)] = 10.0,
    n_simulations: Annotated[int, Query(ge=100, le=10000)] = 2000,
    risk_free_rate: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
) -> dict[str, Any]:
    """Wealth projection with statistically estimated drift and volatility.

    Expected return = market-value weighted, James-Stein shrunk historical
    mean (≥3y data) plus cash at the TL risk-free rate (policy rate unless
    overridden); volatility from the covariance matrix.
    """
    portfolio = await load_portfolio_checked(session, user, portfolio_id)
    if risk_free_rate is None:
        risk_free_rate = market_of(request).risk_free_rate()
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
    summary="Performans özeti (kullanımdan kalkıyor → /report)",
    deprecated=True,
)
async def get_performance(
    request: Request, response: Response, portfolio_id: int, session: SessionDep, user: UserDep
) -> PerformanceOut:
    """Deprecated summary of ``/report``: the same ledger engine, same numbers.

    The old endpoint back-tested today's weights with ``rf = 0`` and disagreed
    with ``/report``; it now projects the ledger report instead.
    """
    portfolio = await load_portfolio_checked(session, user, portfolio_id)
    rep = await portfolio_report(session, market_of(request), portfolio)
    sheet = rep["tear_sheet"]
    successor = f"{request.app.state.settings.api_v1_prefix}/portfolios/{portfolio_id}/report"
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = f'<{successor}>; rel="successor-version"'
    return PerformanceOut(
        portfolio_id=portfolio_id,
        twr_cumulative=rep["twr_cumulative"],
        twr_annualized=rep["twr_annualized"],
        mwr_annualized=rep["mwr_annualized"],
        volatility=sheet.get("volatility"),
        sharpe=sheet.get("sharpe"),
        sortino=sheet.get("sortino"),
        max_drawdown=sheet.get("max_drawdown"),
        var_95_hist=sheet.get("var_95_hist"),
        observations=int(sheet.get("observations", 0)),
        risk_free_rate=float(sheet.get("risk_free_rate", market_of(request).risk_free_rate())),
        period=rep["period"],
        successor=successor,
    )


@router.get("/{portfolio_id}/tax-harvest", summary="Vergi zararı hasadı simülasyonu (bilgi amaçlı)")
async def tax_harvest(
    request: Request, portfolio_id: int, session: SessionDep, user: UserDep
) -> dict[str, Any]:
    """Lots with unrealised losses and the withholding tax they could offset.

    Informational simulation only; rates come from the policy table.
    """
    await load_portfolio_checked(session, user, portfolio_id)
    lots = await open_lots(session, portfolio_id)
    prices = await request.app.state.container.proposals.current_prices(
        [lot.symbol for lot in lots]
    )
    candidates = harvest_candidates(lots, prices)
    return {
        "portfolio_id": portfolio_id,
        "candidates": candidates,
        "total_potential_offset": round(sum(c["potential_tax_offset"] for c in candidates), 2),
        "note": "Bilgi amaçlıdır; vergi oranları yapılandırmadandır, güncel mevzuatı kontrol edin.",
    }
