"""Differentiator endpoints: explanations, regime, stress lab, Black-Litterman
house views, cash Autopilot and behavioural nudges."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from core.deps import (
    CurrentUser,
    SessionDep,
    UserDep,
    actor_of,
    load_customer_checked,
    load_portfolio_checked,
    require_roles,
)
from llm.prompts import DISCLAIMER
from models import BLView, Customer, Goal, Nudge, RebalanceProposal
from models.base import utcnow
from services import behavior
from services.audit import record_audit
from services.market_data.universe import BY_SYMBOL
from services.regime import detect_regime
from services.stress import run_stress, scenario_catalog
from services.suitability.service import effective_level

router = APIRouter(tags=["insights"])
StaffDep = Annotated[CurrentUser, require_roles("admin", "danisman")]


def _c(request: Request) -> Any:
    return request.app.state.container


# ------------------------------------------------------------------ explain


@router.get("/proposals/{proposal_id}/explain", summary="Neden bu dağılım? (kartlar + akıcı metin)")
async def explain(
    request: Request, proposal_id: int, session: SessionDep, user: UserDep, fluent: bool = True
) -> dict[str, Any]:
    proposal = await session.get(RebalanceProposal, proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Öneri bulunamadı.")
    await load_portfolio_checked(session, user, proposal.portfolio_id)
    out: dict[str, Any] = {
        "proposal_id": proposal_id,
        "kartlar": proposal.explanation,
        "disclaimer": DISCLAIMER,
    }
    if fluent and proposal.explanation:
        gen = await _c(request).gateway.generate(
            "explain_allocation", {"kartlar": proposal.explanation}
        )
        out.update({"metin": gen.data.ozet, "maddeler": gen.data.maddeler, "llm_mode": gen.mode})
    return out


# ------------------------------------------------------------------ regime


@router.get("/regime", summary="Piyasa rejimi (boğa / yatay / stres)")
async def regime(request: Request, user: UserDep) -> dict[str, Any]:
    return await detect_regime(_c(request).market)


# ------------------------------------------------------------------ stress


class StressIn(BaseModel):
    scenario: str | None = Field(default=None, description="Katalogdaki senaryo kodu")
    custom: dict[str, float] | None = Field(
        default=None, description="{bist, usdtry, gold, rate} şokları"
    )


@router.get("/stress/scenarios", summary="Stres senaryo kataloğu")
async def scenarios(user: UserDep) -> dict[str, Any]:
    return scenario_catalog()


@router.post("/portfolios/{portfolio_id}/stress", summary="Stres testi")
async def stress(
    request: Request, portfolio_id: int, body: StressIn, session: SessionDep, user: UserDep
) -> dict[str, Any]:
    portfolio = await load_portfolio_checked(session, user, portfolio_id)
    c = _c(request)
    held = {s: float(q) for s, q in (portfolio.holdings or {}).items() if float(q) > 0}
    prices = await c.proposals.current_prices(list(held))
    values = {s: q * prices[s] for s, q in held.items() if s in prices}
    try:
        result = await run_stress(
            c.market, values, float(portfolio.cash), scenario=body.scenario, custom=body.custom
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    goals = (
        (await session.execute(select(Goal).where(Goal.customer_id == portfolio.customer_id)))
        .scalars()
        .all()
    )
    impacts = []
    customer = await session.get(Customer, portfolio.customer_id)
    level = (await effective_level(session, customer)).level if customer else 5
    for g in goals[:3]:
        base_init = float(g.initial_amount)
        shocked = max(0.0, base_init * (1 + result["pnl_orani"]))
        common = {
            "monthly": float(g.monthly_contribution),
            "target": float(g.target_amount_real),
            "years": g.horizon_years,
            "level": g.risk_level or level,
        }
        before = await c.planner.run(
            c.planner.spec(
                initial=base_init, **{k: common[k] for k in ("monthly", "target", "years")}
            ),
            common["level"],
        )
        after = await c.planner.run(
            c.planner.spec(
                initial=shocked, **{k: common[k] for k in ("monthly", "target", "years")}
            ),
            common["level"],
        )
        impacts.append(
            {
                "goal_id": g.id,
                "ad": g.name,
                "once": before["success_probability"],
                "sonra": after["success_probability"],
            }
        )
    result["hedef_etkisi"] = impacts
    return result


# ------------------------------------------------------------------ BL views


class ViewIn(BaseModel):
    symbol: str = Field(min_length=2, max_length=24)
    expected_return: float = Field(ge=-0.9, le=3.0, description="Yıllık, TL bazında")
    confidence: float = Field(ge=0.05, le=0.95)
    rationale: str = Field(default="", max_length=400)
    valid_days: int = Field(default=90, ge=1, le=365)


def view_to_dict(v: BLView) -> dict[str, Any]:
    return {
        "id": v.id,
        "symbol": v.symbol,
        "expected_return": v.expected_return,
        "confidence": v.confidence,
        "rationale": v.rationale,
        "source": v.source,
        "status": v.status,
        "valid_until": v.valid_until.isoformat() if v.valid_until else None,
    }


@router.get("/bl-views", summary="Black-Litterman ev görüşleri")
async def list_views(session: SessionDep, user: StaffDep) -> list[dict[str, Any]]:
    rows = (await session.execute(select(BLView).order_by(BLView.id.desc()))).scalars().all()
    return [view_to_dict(v) for v in rows]


@router.post("/bl-views", status_code=201, summary="Ev görüşü gir (danışman/yönetici — onaylı)")
async def create_view(body: ViewIn, session: SessionDep, user: StaffDep) -> dict[str, Any]:
    if body.symbol not in BY_SYMBOL:
        raise HTTPException(status_code=422, detail="Evrende olmayan sembol.")
    view = BLView(
        symbol=body.symbol,
        expected_return=body.expected_return,
        confidence=body.confidence,
        rationale=body.rationale,
        source="danisman",
        status="onaylandi",
        created_by_user_id=user.id,
        approved_by_user_id=user.id,
        valid_until=utcnow() + timedelta(days=body.valid_days),
    )
    session.add(view)
    await session.commit()
    await session.refresh(view)
    await record_audit(
        actor=actor_of(user),
        actor_role=user.role,
        action="bl_view.create",
        entity_type="bl_view",
        entity_id=view.id,
        payload=view_to_dict(view),
    )
    return view_to_dict(view)


@router.post("/bl-views/suggest", summary="LLM'den görüş ÖNERİSİ al (insan onayı gerekir)")
async def suggest_views(
    request: Request, session: SessionDep, user: StaffDep
) -> list[dict[str, Any]]:
    market = _c(request).market
    symbols = ["XU100.IS", "ALTIN_TL", "USDTRY", "EUROBOND_TL", "TL_TAHVIL"]
    monthly = await market.returns(symbols, freq="M")
    ctx = {
        "piyasa": {
            s: {
                "getiri_12_ay": round(float((1 + monthly[s].tail(12)).prod() - 1), 4),
                "volatilite": round(float(monthly[s].tail(36).std() * 12**0.5), 4),
            }
            for s in symbols
            if s in monthly
        }
    }
    gen = await _c(request).gateway.generate("bl_view_suggestions", ctx)
    out = []
    for v in gen.data.gorusler:
        if v.varlik not in BY_SYMBOL:
            continue
        row = BLView(
            symbol=v.varlik,
            expected_return=v.beklenen_getiri,
            confidence=v.guven,
            rationale=v.gerekce,
            source="llm",
            status="onerildi",
            created_by_user_id=user.id,
        )
        session.add(row)
        out.append(row)
    await session.commit()
    return [view_to_dict(v) for v in out]


@router.post("/bl-views/{view_id}/{decision}", summary="Önerilen görüşü onayla/reddet")
async def decide_view(
    view_id: int, decision: str, session: SessionDep, user: StaffDep
) -> dict[str, Any]:
    if decision not in {"approve", "reject"}:
        raise HTTPException(status_code=404, detail="Geçersiz karar.")
    view = await session.get(BLView, view_id)
    if view is None:
        raise HTTPException(status_code=404, detail="Görüş bulunamadı.")
    view.status = "onaylandi" if decision == "approve" else "reddedildi"
    view.approved_by_user_id = user.id
    if decision == "approve" and view.valid_until is None:
        view.valid_until = utcnow() + timedelta(days=90)
    await session.commit()
    await record_audit(
        actor=actor_of(user),
        actor_role=user.role,
        action=f"bl_view.{decision}",
        entity_type="bl_view",
        entity_id=view_id,
    )
    return view_to_dict(view)


# ------------------------------------------------------------------ autopilot


class AutopilotIn(BaseModel):
    enabled: bool | None = None
    cash_threshold: float | None = Field(default=None, ge=0)
    mode: str | None = Field(default=None, pattern="^(oneri|otomatik)$")


def _ap(s: Any) -> dict[str, Any]:
    return {
        "portfolio_id": s.portfolio_id,
        "enabled": s.enabled,
        "cash_threshold": float(s.cash_threshold),
        "target_symbol": s.target_symbol,
        "mode": s.mode,
        "disclosure": behavior.CONFLICT_DISCLOSURE,
    }


@router.get("/portfolios/{portfolio_id}/autopilot", summary="Nakit Autopilot ayarları")
async def autopilot_get(portfolio_id: int, session: SessionDep, user: UserDep) -> dict[str, Any]:
    await load_portfolio_checked(session, user, portfolio_id)
    return _ap(await behavior.get_settings(portfolio_id))


@router.put("/portfolios/{portfolio_id}/autopilot", summary="Autopilot ayarlarını güncelle")
async def autopilot_put(
    portfolio_id: int, body: AutopilotIn, session: SessionDep, user: UserDep
) -> dict[str, Any]:
    portfolio = await load_portfolio_checked(session, user, portfolio_id)
    row = await behavior.update_settings(portfolio_id, **body.model_dump())
    await record_audit(
        actor=actor_of(user),
        actor_role=user.role,
        action="autopilot.settings",
        entity_type="portfolio",
        entity_id=portfolio_id,
        customer_id=portfolio.customer_id,
        payload=body.model_dump(),
    )
    return _ap(row)


@router.post("/portfolios/{portfolio_id}/autopilot/sweep", summary="Atıl nakdi süpür (şimdi)")
async def autopilot_sweep(
    request: Request, portfolio_id: int, session: SessionDep, user: UserDep
) -> dict[str, Any]:
    await load_portfolio_checked(session, user, portfolio_id)
    return await behavior.sweep(_c(request).proposals, portfolio_id, force=True)


# ------------------------------------------------------------------ nudges


@router.get("/customers/{customer_id}/nudges", summary="Davranışsal dürtmeler")
async def nudges(
    request: Request, customer_id: int, session: SessionDep, user: UserDep, refresh: bool = True
) -> list[dict[str, Any]]:
    await load_customer_checked(session, user, customer_id)
    if refresh:
        await behavior.generate_nudges(_c(request).market, customer_id)
    rows = (
        (
            await session.execute(
                select(Nudge)
                .where(Nudge.customer_id == customer_id, Nudge.dismissed_at.is_(None))
                .order_by(Nudge.id.desc())
            )
        )
        .scalars()
        .all()
    )
    return [behavior.nudge_to_dict(n) for n in rows]


@router.post("/nudges/{nudge_id}/dismiss", summary="Dürtmeyi kapat")
async def dismiss(nudge_id: int, session: SessionDep, user: UserDep) -> dict[str, Any]:
    nudge = await session.get(Nudge, nudge_id)
    if nudge is None:
        raise HTTPException(status_code=404, detail="Dürtme bulunamadı.")
    await load_customer_checked(session, user, nudge.customer_id)
    nudge.dismissed_at = utcnow()
    await session.commit()
    return behavior.nudge_to_dict(nudge)


@router.get(
    "/customers/{customer_id}/behavior-gap",
    summary="Davranış açığı (yatırımcı vs portföy getirisi)",
)
async def behavior_gap(
    request: Request, customer_id: int, session: SessionDep, user: UserDep
) -> dict[str, Any]:
    from models import Portfolio
    from services.analytics.history import portfolio_report

    await load_customer_checked(session, user, customer_id)
    rows = (
        (await session.execute(select(Portfolio).where(Portfolio.customer_id == customer_id)))
        .scalars()
        .all()
    )
    out = []
    for p in rows:
        rep = await portfolio_report(session, _c(request).market, p)
        # Karşılaştırma aynı birimde olmalı: yıllık MWR − yıllık TWR (bir yıldan kısa
        # dönemlerde ikisi de yıllıklandırılmaz → açık hesaplanmaz).
        twr_ann, mwr_ann = rep["twr_annualized"], rep["mwr_annualized"]
        out.append(
            {
                "portfolio_id": p.id,
                "twr_cumulative": rep["twr_cumulative"],
                "twr_annualized": twr_ann,
                "mwr_annualized": mwr_ann,
                "period": rep["period"],
                "behavior_gap": (mwr_ann - twr_ann)
                if mwr_ann is not None and twr_ann is not None
                else None,
            }
        )
    return {
        "customer_id": customer_id,
        "portfolios": out,
        "note": "Yıllık MWR − yıllık TWR: negatifse zamanlama kararları getiriyi düşürmüş "
        "olabilir. Bir yıldan kısa dönemler yıllıklandırılmaz.",
    }
