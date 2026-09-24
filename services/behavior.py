"""Cash Autopilot and behavioural nudges.

* **Autopilot** — cash above the customer's threshold is swept into the TL
  money-market fund. In ``oneri`` mode a proposal awaits approval; in
  ``otomatik`` mode the customer has pre-authorised sweeps, so the system
  approves and executes (audited). A conflict-of-interest disclosure is
  returned with every sweep (idle cash earns the platform, not the client).
* **Nudges** — market drop (panic-sell guard with a cooling-off period and a
  historical "cost of panic selling" figure), idle cash, expiring risk
  profile and goals drifting below their success threshold.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import numpy as np
from sqlalchemy import select

from core.database import session_factory
from core.logging import get_logger
from core.policy import get_policy
from llm.fmt import pct, tl
from models import AutopilotSetting, Customer, Goal, Nudge, Portfolio
from models.base import utcnow
from services.audit import record_audit
from services.market_data.service import MarketDataService
from services.rebalancing.proposals import ProposalService
from services.suitability.service import effective_level

logger = get_logger("otonom.behavior")

CONFLICT_DISCLOSURE = (
    "Çıkar çatışması açıklaması: Hesabınızda atıl bekleyen nakit, platform için faiz geliri "
    "yaratabilir. Autopilot bu nakdi sizin adınıza para piyasası fonuna yönlendirir; "
    "fonun yönetim ücreti fiyatına yansır."
)


async def get_settings(portfolio_id: int) -> AutopilotSetting:
    async with session_factory() as session:
        row = (
            await session.execute(
                select(AutopilotSetting).where(AutopilotSetting.portfolio_id == portfolio_id)
            )
        ).scalar_one_or_none()
        if row is None:
            policy = get_policy()
            row = AutopilotSetting(
                portfolio_id=portfolio_id,
                enabled=False,
                cash_threshold=Decimal(str(policy.autopilot["default_threshold"])),
                target_symbol=str(policy.autopilot["target_symbol"]),
                mode="oneri",
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return row


async def update_settings(portfolio_id: int, **fields: Any) -> AutopilotSetting:
    await get_settings(portfolio_id)
    async with session_factory() as session:
        row = (
            await session.execute(
                select(AutopilotSetting).where(AutopilotSetting.portfolio_id == portfolio_id)
            )
        ).scalar_one()
        for k, v in fields.items():
            if v is not None:
                setattr(row, k, Decimal(str(v)) if k == "cash_threshold" else v)
        await session.commit()
        await session.refresh(row)
        return row


async def sweep(
    proposals: ProposalService, portfolio_id: int, *, force: bool = False
) -> dict[str, Any]:
    """Sweep idle cash above the threshold (proposal or auto-execution)."""
    setting = await get_settings(portfolio_id)
    if not setting.enabled and not force:
        return {"swept": False, "reason": "Autopilot kapalı.", "disclosure": CONFLICT_DISCLOSURE}
    async with session_factory() as session:
        portfolio = await session.get(Portfolio, portfolio_id)
        assert portfolio is not None
        cash = float(portfolio.cash)
    excess = cash - float(setting.cash_threshold)
    if excess < float(get_policy().autopilot["min_sweep_amount"]):
        return {"swept": False, "reason": "Eşik üstü nakit yok.", "disclosure": CONFLICT_DISCLOSURE}
    amount = round(excess * 0.995, 2)  # maliyet payı
    proposal = await proposals.create_custom(
        portfolio_id,
        {setting.target_symbol: amount},
        source="autopilot",
        trigger="cash",
        rationale=f"Autopilot: eşik üstü {tl(amount, 2)} nakit {setting.target_symbol} fonuna yönlendirilir.",
    )
    executed = False
    if setting.mode == "otomatik":
        await proposals.approve(proposal.id, user_id=None, role="system")
        await proposals.execute(proposal.id, actor="autopilot")
        executed = True
        await record_audit(
            actor="autopilot",
            action="autopilot.auto_execute",
            entity_type="rebalance_proposal",
            entity_id=proposal.id,
            customer_id=proposal.customer_id,
            payload={"amount": amount},
        )
    return {
        "swept": True,
        "proposal_id": proposal.id,
        "amount": amount,
        "executed": executed,
        "disclosure": CONFLICT_DISCLOSURE,
    }


async def panic_sell_cost(market: MarketDataService, level: int) -> dict[str, Any]:
    """Average 12-month forward return of the model portfolio after −5 % months."""
    from services.market_data.universe import CLASS_REPRESENTATIVE

    policy = get_policy()
    weights = policy.model_weights(level)
    symbols = [CLASS_REPRESENTATIVE[c] for c in weights]
    rets = await market.returns(symbols + ["XU100.IS"], freq="M")
    w = np.array([weights[c] for c in weights])
    port = rets[symbols].fillna(0.0).to_numpy() @ (w / w.sum())
    bist = rets["XU100.IS"].to_numpy()
    trigger = float(policy.nudges["market_drop_trigger"])
    fwd = [
        float(np.prod(1 + port[i + 1 : i + 13]) - 1)
        for i in range(len(bist) - 12)
        if bist[i] <= trigger
    ]
    return {
        "episodes": len(fwd),
        "avg_12m_after_drop": float(np.mean(fwd)) if fwd else None,
        "share_positive": float(np.mean([f > 0 for f in fwd])) if fwd else None,
    }


async def generate_nudges(market: MarketDataService, customer_id: int) -> list[Nudge]:
    """Create fresh nudges for a customer (deduplicated per cooling-off period)."""
    policy = get_policy()
    cfg = policy.nudges
    created: list[Nudge] = []
    async with session_factory() as session:
        customer = await session.get(Customer, customer_id)
        if customer is None:
            return []
        level = await effective_level(session, customer)
        portfolios = (
            (await session.execute(select(Portfolio).where(Portfolio.customer_id == customer_id)))
            .scalars()
            .all()
        )
        goals = (
            (await session.execute(select(Goal).where(Goal.customer_id == customer_id)))
            .scalars()
            .all()
        )
        since = utcnow() - timedelta(hours=float(cfg["cooling_off_hours"]))
        recent = {
            (n.kind, n.portfolio_id)
            for n in (
                await session.execute(
                    select(Nudge).where(Nudge.customer_id == customer_id, Nudge.created_at >= since)
                )
            )
            .scalars()
            .all()
        }

        def add(
            kind: str,
            severity: str,
            title: str,
            message: str,
            data: dict[str, Any],
            pid: int | None = None,
        ) -> None:
            if (kind, pid) in recent:
                return
            n = Nudge(
                customer_id=customer_id,
                portfolio_id=pid,
                kind=kind,
                severity=severity,
                title=title,
                message=message,
                data=data,
            )
            session.add(n)
            created.append(n)

        bist = await market.returns(["XU100.IS"], freq="M")
        last_month = float(bist["XU100.IS"].iloc[-1]) if not bist.empty else 0.0
        if last_month <= float(cfg["market_drop_trigger"]):
            cost = await panic_sell_cost(market, level.level)
            avg = cost["avg_12m_after_drop"]
            add(
                "piyasa_dususu",
                "warn",
                "Piyasa düştü — acele etmeyin",
                f"BIST son ayda {pct(last_month)} değer kaybetti. Satış kararından önce "
                f"{int(cfg['cooling_off_hours'])} saat düşünmenizi öneririz."
                + (
                    f" Geçmişte benzer düşüşlerden sonraki 12 ayda model portföy ortalama {pct(avg)} getiri sağladı."
                    if avg is not None
                    else ""
                ),
                {"bist_1m": last_month, **cost},
            )
        for p in portfolios:
            prices = await market.history(
                [s for s, q in (p.holdings or {}).items() if float(q) > 0] or ["TL_PPF"]
            )
            last = prices.iloc[-1] if not prices.empty else {}
            invested = sum(
                float(q) * float(last.get(s, 0.0)) for s, q in (p.holdings or {}).items()
            )
            total = invested + float(p.cash)
            ratio = float(p.cash) / total if total else 0.0
            if total > 0 and ratio > float(cfg["idle_cash_ratio"]):
                add(
                    "atil_nakit",
                    "info",
                    "Atıl nakdiniz var",
                    f"Portföyünüzün {pct(ratio)} kadarı nakitte bekliyor. Autopilot ile para piyasası fonuna yönlendirebilirsiniz.",
                    {"cash_ratio": ratio},
                    p.id,
                )
        if level.source != "profil" or level.expired or level.renewal_due:
            add(
                "profil_yenileme",
                "warn",
                "Risk profilinizi güncelleyin",
                "Uygunluk testiniz eksik veya süresi dolmak üzere; önerilerin size uygun kalması için anketi yenileyin.",
                level.to_dict(),
            )
        threshold = float(cfg["goal_gap_probability"])
        for g in goals:
            sim = g.last_simulation or {}
            prob = sim.get("success_probability")
            if prob is not None and prob < threshold:
                add(
                    "hedef_sapmasi",
                    "warn",
                    f"'{g.name}' hedefinden sapma",
                    f"Hedefe ulaşma olasılığı {pct(prob)}; aylık katkıyı {tl(sim.get('required_monthly_contribution', 0))} seviyesine çıkarmayı değerlendirin.",
                    {"goal_id": g.id, **sim},
                )
        await session.commit()
        for n in created:
            await session.refresh(n)
    return created


def nudge_to_dict(n: Nudge) -> dict[str, Any]:
    return {
        "id": n.id,
        "customer_id": n.customer_id,
        "portfolio_id": n.portfolio_id,
        "kind": n.kind,
        "severity": n.severity,
        "title": n.title,
        "message": n.message,
        "data": n.data,
        "created_at": n.created_at.isoformat(),
        "dismissed_at": n.dismissed_at.isoformat() if n.dismissed_at else None,
    }


__all__ = [
    "CONFLICT_DISCLOSURE",
    "generate_nudges",
    "get_settings",
    "nudge_to_dict",
    "panic_sell_cost",
    "sweep",
    "update_settings",
]
