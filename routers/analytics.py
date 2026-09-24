"""Analytics router — valuation & performance endpoints.

GET /api/v1/portfolios/{id}/valuation        → mark-to-market + unrealized P&L
GET /api/v1/portfolios/{id}/performance      → Sharpe / VaR / max drawdown

These are read-only and cannot modify any portfolio state; they give advisors
and customers a live view derived from real market data (yfinance) through
:class:`services.analytics_service.AnalyticsService`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_session
from models import Portfolio, Transaction
from services.analytics_service import AnalyticsService, MonteCarloProjection
from services.market_service import MarketService, YFinanceSource

router = APIRouter(prefix="/portfolios", tags=["portfolios"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


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


async def _get_portfolio_or_404(session: AsyncSession, portfolio_id: int) -> Portfolio:
    portfolio = await session.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Portfolio {portfolio_id} bulunamadı.",
        )
    return portfolio


async def _avg_cost_basis(session: AsyncSession, portfolio_id: int) -> dict[str, float]:
    """Simple average-cost basis: total spent / total bought per ticker.

    BUY adds cost, SELL removes proportionally from both quantity and cost.
    This keeps a consistent (FIFO-free) cost basis for P&L reporting.
    """
    result = await session.execute(
        select(Transaction).where(Transaction.portfolio_id == portfolio_id)
    )
    basis: dict[str, float] = {}
    for tx in result.scalars():
        sym = tx.ticker
        qty_before, cost_before = basis.get(sym, (0.0, 0.0))
        if tx.side == "BUY":
            new_qty = qty_before + tx.quantity
            new_cost = cost_before + tx.total_amount
            basis[sym] = (new_qty, new_cost)
        else:  # SELL: reduce proportionally
            if qty_before > 0:
                ratio = tx.quantity / qty_before
                new_cost = cost_before * (1.0 - ratio)
                new_qty = max(qty_before - tx.quantity, 0.0)
                basis[sym] = (new_qty, new_cost)
    return {sym: cost / qty for sym, (qty, cost) in basis.items() if qty > 1e-12}


@router.get(
    "/{portfolio_id}/valuation",
    response_model=ValuationOut,
    summary="Portföyü güncel piyasa fiyatlarıyla değerle",
    description=(
        "Mark-to-market değerleme: nakit + Σ (adet × güncel fiyat), her varlık "
        "için ağırlık ve basit ortalama maliyete göre realize edilmemiş kâr/zarar."
    ),
)
async def get_valuation(
    request: Request,
    portfolio_id: int,
    session: SessionDep,
) -> ValuationOut:
    """Compute live mark-to-market valuation for a portfolio."""
    portfolio = await _get_portfolio_or_404(session, portfolio_id)

    del request
    market = MarketService(YFinanceSource())
    tickers = [t for t in portfolio.holdings if portfolio.holdings.get(t, 0) > 0]
    snapshots = await market.fetch_snapshots(tickers) if tickers else {}

    basis = await _avg_cost_basis(session, portfolio_id)
    result = AnalyticsService().value_portfolio(
        portfolio_id=portfolio.id,
        cash=float(portfolio.cash),
        holdings=dict(portfolio.holdings or {}),
        snapshots=snapshots,
        cost_basis=basis,
    )
    return ValuationOut(**result.to_dict())


@router.get(
    "/{portfolio_id}/drift",
    summary="Portföy sapmasını hedef ağırlıklara göre ölç",
    description=(
        "Band tabanlı yeniden dengeleme göstergesi: her varlığın gerçek "
        "ağırlığı hedef ağırlığa göre ne kadar sapmış; sapma trigger band'ını "
        "aşan varlık varsa needs_rebalance=true döner."
    ),
)
async def get_drift(
    request: Request,
    portfolio_id: int,
    session: SessionDep,
    trigger_band: Annotated[float, Query(ge=0.0, le=0.5)] = 0.05,
) -> dict[str, object]:
    """Return drift analysis vs an optional target allocation."""
    portfolio = await _get_portfolio_or_404(session, portfolio_id)
    del request

    market = MarketService(YFinanceSource())
    tickers = [t for t in portfolio.holdings if portfolio.holdings.get(t, 0) > 0]
    snapshots = await market.fetch_snapshots(tickers) if tickers else {}

    return AnalyticsService().drift_analysis(
        portfolio_id=portfolio.id,
        cash=float(portfolio.cash),
        holdings=dict(portfolio.holdings or {}),
        snapshots=snapshots,
        target_weights={},
        trigger_band=trigger_band,
    )


@router.get(
    "/{portfolio_id}/projection",
    summary="Monte Carlo ile hedef projeksiyon (gelecek değer senaryoları)",
    description=(
        "Portföyün güncel değeri, beklenen yıllık getirisi ve oynaklığı ile "
        "hedef ufuk sonundaki 5./50./95. dilim değerlerini stokastik olarak "
        "simüle eder (geometric Brownian motion)."
    ),
)
async def get_projection(
    request: Request,
    portfolio_id: int,
    session: SessionDep,
    horizon_years: Annotated[float, Query(ge=0.1, le=50)] = 10.0,
    n_simulations: Annotated[int, Query(ge=100, le=10000)] = 2000,
) -> dict[str, object]:
    """Return Monte Carlo wealth projection for the portfolio."""
    portfolio = await _get_portfolio_or_404(session, portfolio_id)
    del request

    market = MarketService(YFinanceSource())
    tickers = [t for t in portfolio.holdings if portfolio.holdings.get(t, 0) > 0]
    snapshots = await market.fetch_snapshots(tickers) if tickers else {}
    valuation = AnalyticsService().value_portfolio(
        portfolio_id=portfolio.id,
        cash=float(portfolio.cash),
        holdings=dict(portfolio.holdings or {}),
        snapshots=snapshots,
    )
    vol = 0.15
    expected = 0.06
    if tickers:
        vols = [
            snapshots[t].volatility_annualized
            for t in tickers
            if t in snapshots and snapshots[t].volatility_annualized > 0
        ]
        moms = [snapshots[t].momentum_1m for t in tickers if t in snapshots]
        if vols:
            vol = float(sum(vols) / len(vols))
        if moms:
            expected = max(-0.5, min(0.5, float(sum(moms) / len(moms)) / 12.0))

    proj = MonteCarloProjection(
        current_value=valuation.total_value,
        annual_return=expected,
        annual_vol=vol,
        horizon_years=horizon_years,
        n_simulations=n_simulations,
    )
    return proj.run()


@router.get(
    "/{portfolio_id}/performance",
    response_model=PerformanceOut,
    summary="Portföy performans metrikleri (Sharpe, VaR, max drawdown)",
)
async def get_performance(
    request: Request,
    portfolio_id: int,
    session: SessionDep,
    risk_free_rate: Annotated[float, Query(ge=0.0, le=0.5)] = 0.0,
) -> PerformanceOut:
    """Compute performance metrics from the portfolio's recent price history.

    The market service assembles an aligned daily-returns frame for the held
    tickers; performance analytics are then computed over that frame.
    """
    portfolio = await _get_portfolio_or_404(session, portfolio_id)

    del request
    market = MarketService(YFinanceSource())
    tickers = [t for t in portfolio.holdings if portfolio.holdings.get(t, 0) > 0]
    snapshots = await market.fetch_snapshots(tickers) if tickers else {}
    returns = market.build_returns_frame(snapshots)

    metrics = AnalyticsService().performance_metrics(returns, risk_free_rate=risk_free_rate)
    return PerformanceOut(portfolio_id=portfolio_id, **metrics)
