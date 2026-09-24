"""Copilot tools — the permission envelope.

Whitelisted, read-mostly tools. The only state-changing tool,
``preview_rebalance``, creates a proposal that still needs human approval;
**there is no execution tool**, so the copilot can never trade.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.database import session_factory
from models import Customer, Goal, Portfolio
from services.optimization.service import OptimizationRequest
from services.regime import cached_regime
from services.stress import run_stress, scenario_catalog
from services.suitability.service import effective_level

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": props, "additionalProperties": False},
        },
    }
    for name, desc, props in [
        ("get_portfolio", "Portföyün değeri, nakdi ve pozisyonları.", {}),
        ("get_risk_profile", "Müşterinin risk seviyesi ve profil geçerliliği.", {}),
        (
            "run_optimization",
            "Risk seviyesine uygun hedef dağılım (önizleme).",
            {"method": {"type": "string"}},
        ),
        (
            "simulate_goal",
            "Müşterinin (ilk) hedefi için Monte Carlo başarı olasılığı.",
            {"goal_id": {"type": "integer"}},
        ),
        (
            "run_stress",
            "Stres senaryosu (ör. usdtry_up_30, bist_down_25, rate_up_500bp).",
            {"scenario": {"type": "string"}},
        ),
        ("preview_rebalance", "Yeniden dengeleme ÖNERİSİ oluşturur (onay gerekir, yürütmez).", {}),
        ("explain_allocation", "Önerilen/son dağılımın 'neden' kartları.", {}),
        ("get_market_summary", "Piyasa ve makro özet (TÜFE, faiz, rejim).", {}),
    ]
]
ALLOWED_TOOLS = frozenset(t["function"]["name"] for t in TOOL_SPECS)


@dataclass
class CopilotContext:
    container: Any
    customer_id: int
    portfolio_id: int | None
    user_id: int | None
    role: str | None


async def _portfolio(ctx: CopilotContext) -> Portfolio | None:
    async with session_factory() as s:
        if ctx.portfolio_id:
            return await s.get(Portfolio, ctx.portfolio_id)
        from sqlalchemy import select

        return (
            await s.execute(
                select(Portfolio)
                .where(Portfolio.customer_id == ctx.customer_id)
                .order_by(Portfolio.id)
                .limit(1)
            )
        ).scalar_one_or_none()


async def execute_tool(name: str, args: dict[str, Any], ctx: CopilotContext) -> dict[str, Any]:
    """Run a whitelisted tool and return a JSON-serialisable result."""
    if name not in ALLOWED_TOOLS:
        return {"hata": f"'{name}' aracı yetki zarfının dışında."}
    c = ctx.container
    async with session_factory() as s:
        customer = await s.get(Customer, ctx.customer_id)
        level = await effective_level(s, customer) if customer else None
    portfolio = await _portfolio(ctx)
    if name == "get_risk_profile":
        if level is None:
            return {"hata": "Müşteri bulunamadı."}
        return {
            "seviye": level.level,
            "kategori": level.label,
            "kaynak": level.source,
            "gecerlilik": level.valid_until.date().isoformat() if level.valid_until else None,
        }
    if name == "get_market_summary":
        rets = await c.market.returns(["XU100.IS", "ALTIN_TL", "USDTRY"], freq="M")
        reg = await cached_regime(c)
        return {
            "piyasa": {s: {"getiri_1_ay": round(float(rets[s].iloc[-1]), 4)} for s in rets.columns},
            "makro": {
                "politika_faizi": round(c.market.risk_free_rate(), 4),
                "tufe_yillik": round(c.market.inflation_yoy(), 4),
            },
            "rejim": {"etiket": reg.get("label")},
        }
    if name == "run_optimization":
        res = await c.optimizer.optimize(
            OptimizationRequest(level=level.level if level else 5, method=args.get("method"))
        )
        return {
            "yontem": res.method_label,
            "agirliklar": {k: round(v, 4) for k, v in res.weights.items()},
            "volatilite": round(res.volatility, 4),
        }
    if portfolio is None:
        return {"hata": "Portföy bulunamadı."}
    held = {k: float(v) for k, v in (portfolio.holdings or {}).items() if float(v) > 0}
    prices = await c.proposals.current_prices(list(held))
    values = {k: q * prices[k] for k, q in held.items() if k in prices}
    if name == "get_portfolio":
        return {
            "toplam_deger": round(sum(values.values()) + float(portfolio.cash), 2),
            "nakit": round(float(portfolio.cash), 2),
            "pozisyonlar": {k: round(v, 2) for k, v in values.items()},
        }
    if name == "run_stress":
        scenario = str(args.get("scenario") or "bist_down_25")
        catalog = scenario_catalog()
        if scenario not in catalog["factor"] and scenario not in catalog["historical"]:
            scenario = "bist_down_25"
        return await run_stress(c.market, values, float(portfolio.cash), scenario=scenario)
    if name == "simulate_goal":
        async with session_factory() as s:
            from sqlalchemy import select

            stmt = select(Goal).where(Goal.customer_id == ctx.customer_id)
            if args.get("goal_id"):
                stmt = stmt.where(Goal.id == int(args["goal_id"]))
            goal = (
                await s.execute(stmt.order_by(Goal.priority, Goal.id).limit(1))
            ).scalar_one_or_none()
        if goal is None:
            return {"hata": "Tanımlı hedef yok; önce bir hedef oluşturun."}
        res = await c.planner.run(
            c.planner.spec(
                initial=float(goal.initial_amount),
                monthly=float(goal.monthly_contribution),
                target=float(goal.target_amount_real),
                years=goal.horizon_years,
            ),
            goal.risk_level or (level.level if level else 5),
        )
        return {
            "hedef": {"ad": goal.name, "tur": goal.goal_type},
            "basari_olasiligi": res["success_probability"],
            "gerekli_aylik_katki": res["required_monthly_contribution"],
            "p50_reel": res["p50_real"],
        }
    if name == "preview_rebalance":
        outcome = await c.proposals.create(
            portfolio.id, actor="copilot", source="copilot", trigger="manual"
        )
        if outcome.proposal is None:
            return {"mesaj": outcome.message, "emirler": [], "toplam_maliyet": 0.0}
        p = outcome.proposal
        return {
            "oneri_id": p.id,
            "durum": p.status,
            "emirler": [
                {"sembol": o["symbol"], "yon": o["side"], "tutar": o["amount"]} for o in p.orders
            ],
            "toplam_maliyet": round(float(p.estimated_cost), 2),
        }
    if name == "explain_allocation":
        from sqlalchemy import select

        from models import RebalanceProposal

        async with session_factory() as s:
            prop = (
                await s.execute(
                    select(RebalanceProposal)
                    .where(RebalanceProposal.portfolio_id == portfolio.id)
                    .order_by(RebalanceProposal.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if prop is None:
            return {"hata": "Henüz bir dağılım önerisi yok."}
        return {"kartlar": [{"baslik": k["baslik"], "metin": k["metin"]} for k in prop.explanation]}
    return {"hata": "Araç uygulanamadı."}


__all__ = ["ALLOWED_TOOLS", "TOOL_SPECS", "CopilotContext", "execute_tool"]
