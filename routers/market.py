"""Market router: data source status, instrument universe, macro and prices."""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from core.deps import SessionDep, UserDep
from models import Instrument, ModelPortfolio
from services.market_data.service import MarketDataService

router = APIRouter(prefix="/market", tags=["market"])


def _market(request: Request) -> MarketDataService:
    return cast(MarketDataService, request.app.state.container.market)


@router.get("/status", summary="Veri kaynağı durumu (Canlı / Önbellek)")
async def market_status(request: Request) -> dict[str, Any]:
    """Public badge endpoint: live vs snapshot data, snapshot end date."""
    market = _market(request)
    status_fn = getattr(market, "data_status", None)
    return status_fn() if callable(status_fn) else {"source": "custom"}


@router.get("/instruments", summary="Yatırım evreni")
async def instruments(session: SessionDep, user: UserDep) -> list[dict[str, Any]]:
    """Active instruments with asset class, risk score and costs."""
    rows = (
        (
            await session.execute(
                select(Instrument).where(Instrument.is_active.is_(True)).order_by(Instrument.id)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "symbol": r.symbol,
            "name": r.name,
            "asset_class": r.asset_class,
            "currency": r.currency,
            "source": r.source,
            "risk_score": r.risk_score,
            "expense_ratio": r.expense_ratio,
            "liquidity_days": r.liquidity_days,
            "sector": r.sector,
            "esg_member": r.esg_member,
            "esg_score": r.esg_score,
            "description": r.description,
        }
        for r in rows
    ]


@router.get("/model-portfolios", summary="10 seviyeli model portföy kütüphanesi")
async def model_portfolios(session: SessionDep, user: UserDep) -> list[dict[str, Any]]:
    """Active model portfolios (asset-class weights) of the current policy."""
    rows = (
        (
            await session.execute(
                select(ModelPortfolio)
                .where(ModelPortfolio.is_active.is_(True))
                .order_by(ModelPortfolio.level)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "level": r.level,
            "version": r.version,
            "name": r.name,
            "class_weights": r.class_weights,
            "expected_return": r.expected_return,
            "expected_volatility": r.expected_volatility,
        }
        for r in rows
    ]


@router.get("/macro", summary="Makro göstergeler (TÜFE, politika faizi, USDTRY)")
async def macro(
    request: Request, user: UserDep, months: Annotated[int, Query(ge=1, le=240)] = 36
) -> dict[str, Any]:
    """Latest risk-free rate, YoY inflation and the recent monthly series."""
    market = _market(request)
    frame = market.macro().tail(months)
    return {
        "risk_free_rate": market.risk_free_rate(),
        "inflation_yoy": market.inflation_yoy(),
        "series": [
            {
                "date": idx.date().isoformat(),
                **{k: (None if v != v else float(v)) for k, v in row.items()},
            }
            for idx, row in frame.iterrows()
        ],
    }


@router.get("/prices", summary="Fiyat geçmişi")
async def prices(
    request: Request,
    user: UserDep,
    symbols: Annotated[str, Query(min_length=1, max_length=400)],
    days: Annotated[int, Query(ge=5, le=4000)] = 252,
    currency: Annotated[str, Query(pattern="^(TRY|USD)$")] = "TRY",
) -> dict[str, Any]:
    """Close prices (TL or USD based) for comma separated symbols."""
    wanted = [s.strip() for s in symbols.split(",") if s.strip()][:25]
    market = _market(request)
    panel = await market.history(wanted + (["USDTRY"] if currency == "USD" else []))
    if panel.empty:
        raise HTTPException(status_code=404, detail="Fiyat verisi bulunamadı.")
    if currency == "USD" and "USDTRY" in panel.columns:
        panel = panel[[c for c in wanted if c in panel.columns]].div(panel["USDTRY"], axis=0)
    panel = panel[[c for c in wanted if c in panel.columns]].tail(days)
    return {
        "currency": currency,
        "dates": [d.date().isoformat() for d in panel.index],
        "series": {
            c: [round(float(v), 6) for v in panel[c].ffill().bfill()] for c in panel.columns
        },
    }
