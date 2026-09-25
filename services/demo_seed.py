"""Demo data: three personas with profiles, goals, two years of trading
history and a pending rebalance proposal, plus an advisor account.

Idempotent — skipped when the demo users already exist. Used by
``scripts/seed_demo.py`` and on startup when ``SEED_DEMO=true`` (Compose).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from core.database import session_factory
from core.logging import get_logger
from core.security import hash_password
from models import CashFlow, Customer, Goal, Portfolio, User
from services.ledger import apply_trade
from services.suitability.questionnaire import QUESTIONS
from services.suitability.scoring import score_answers
from services.suitability.service import save_profile

logger = get_logger("otonom.seed")

DEMO_PW = "Demo!2345"
ADVISOR_USER = ("danisman", "Danisman!2345")


@dataclass(frozen=True)
class Persona:
    username: str
    full_name: str
    email: str
    income: float
    horizon: int
    tilt: int  # anket cevaplarında seçilecek seçenek sırası (0=temkinli … 4=atak)
    deposit: float
    monthly: float
    basket: dict[str, float]
    goals: tuple[dict[str, Any], ...]


PERSONAS: tuple[Persona, ...] = (
    Persona(
        "demo.genc",
        "Deniz Aksoy",
        "deniz.aksoy@demo.local",
        85_000,
        25,
        4,
        300_000,
        15_000,
        {
            "XU100.IS": 0.35,
            "THYAO.IS": 0.15,
            "ASELS.IS": 0.10,
            "ALTIN_TL": 0.15,
            "USDTRY": 0.10,
            "TL_PPF": 0.15,
        },
        (
            {
                "goal_type": "emeklilik",
                "name": "Erken emeklilik",
                "target_amount_real": 12_000_000,
                "horizon_years": 25,
                "initial_amount": 300_000,
                "monthly_contribution": 15_000,
            },
        ),
    ),
    Persona(
        "demo.ev",
        "Elif Yıldız",
        "elif.yildiz@demo.local",
        60_000,
        6,
        2,
        450_000,
        12_000,
        {
            "TL_PPF": 0.30,
            "TL_TAHVIL": 0.25,
            "ALTIN_TL": 0.20,
            "XU100.IS": 0.15,
            "EUROBOND_TL": 0.10,
        },
        (
            {
                "goal_type": "ev",
                "name": "Ev peşinatı",
                "target_amount_real": 1_500_000,
                "horizon_years": 5,
                "initial_amount": 450_000,
                "monthly_contribution": 12_000,
            },
            {
                "goal_type": "acil_durum",
                "name": "Acil durum fonu",
                "target_amount_real": 180_000,
                "horizon_years": 1,
                "initial_amount": 60_000,
                "monthly_contribution": 8_000,
            },
        ),
    ),
    Persona(
        "demo.emekli",
        "Mehmet Kaya",
        "mehmet.kaya@demo.local",
        40_000,
        4,
        0,
        900_000,
        0,
        {"TL_PPF": 0.60, "TL_TAHVIL": 0.20, "ALTIN_TL": 0.10, "EUROBOND_TL": 0.10},
        (
            {
                "goal_type": "diger",
                "name": "Emeklilik gelir tamponu",
                "target_amount_real": 1_000_000,
                "horizon_years": 3,
                "initial_amount": 900_000,
                "monthly_contribution": 0,
            },
        ),
    ),
)


def persona_answers(tilt: int, horizon: int) -> dict[str, str]:
    """Consistent questionnaire answers leaning conservative (0) or bold (4)."""
    out: dict[str, str] = {}
    for q in QUESTIONS:
        opts = sorted(q.options, key=lambda o: o.score)
        idx = min(len(opts) - 1, round(tilt * (len(opts) - 1) / 4))
        out[q.id] = opts[idx].value
    out["vade"] = "v4" if horizon > 10 else "v3" if horizon > 5 else "v2"
    out["borc_orani"], out["acil_durum_fonu"] = "d3", "a2" if tilt < 4 else "a3"
    return out


async def seed_demo(container: Any) -> dict[str, Any]:
    """Create demo users, customers, history and pending proposals."""
    async with session_factory() as s:
        if (
            await s.execute(select(User).where(User.username == PERSONAS[0].username))
        ).scalar_one_or_none():
            return {"seeded": False, "reason": "zaten mevcut"}
        advisor = User(
            username=ADVISOR_USER[0],
            password_hash=hash_password(ADVISOR_USER[1]),
            role="danisman",
            display_name="Ayşe Danışman",
        )
        s.add(advisor)
        await s.flush()
        advisor_id = advisor.id
        await s.commit()

    panel = await container.market.history(sorted({sym for p in PERSONAS for sym in p.basket}))
    start = panel.index[-500]
    created: list[int] = []
    for persona in PERSONAS:
        async with session_factory() as s:
            customer = Customer(
                full_name=persona.full_name,
                email=persona.email,
                investment_horizon_years=persona.horizon,
                monthly_income=Decimal(str(persona.income)),
                advisor_user_id=advisor_id,
                financial_goal=persona.goals[0]["name"],
            )
            s.add(customer)
            await s.flush()
            answers = persona_answers(persona.tilt, persona.horizon)
            await save_profile(s, customer, answers, score_answers(answers), None)
            portfolio = Portfolio(
                customer_id=customer.id, name="Ana Portföy", cash=Decimal("0"), holdings={}
            )
            s.add(portfolio)
            await s.flush()
            s.add(
                User(
                    username=persona.username,
                    password_hash=hash_password(DEMO_PW),
                    role="musteri",
                    customer_id=customer.id,
                    display_name=persona.full_name,
                )
            )
            # İlk yatırım (2 yıl önce) + aylık katkılar
            day0: datetime = start.to_pydatetime()
            portfolio.cash = Decimal(str(persona.deposit))
            s.add(
                CashFlow(
                    portfolio_id=portfolio.id,
                    kind="DEPOSIT",
                    amount=Decimal(str(persona.deposit)),
                    occurred_at=day0,
                )
            )
            for sym, w in persona.basket.items():
                price = float(panel.loc[start, sym])
                await apply_trade(
                    s,
                    portfolio,
                    symbol=sym,
                    side="BUY",
                    quantity=persona.deposit * w * 0.995 / price,
                    price=price,
                    reason="seed",
                    executed_at=day0,
                )
            for month in range(1, 24):
                if not persona.monthly:
                    break
                when = panel.index[
                    min(len(panel.index) - 1, panel.index.get_loc(start) + month * 21)
                ]
                amount = Decimal(str(persona.monthly))
                portfolio.cash = portfolio.cash + amount
                s.add(
                    CashFlow(
                        portfolio_id=portfolio.id,
                        kind="DEPOSIT",
                        amount=amount,
                        occurred_at=when.to_pydatetime(),
                    )
                )
                sym = max(persona.basket, key=lambda s: persona.basket[s])
                price = float(panel.loc[when, sym])
                await apply_trade(
                    s,
                    portfolio,
                    symbol=sym,
                    side="BUY",
                    quantity=persona.monthly * 0.99 / price,
                    price=price,
                    reason="seed",
                    executed_at=when.to_pydatetime() + timedelta(hours=1),
                )
            portfolio.cash = portfolio.cash + Decimal(
                "25000"
            )  # atıl nakit (Autopilot/dürtme demosu)
            s.add(
                CashFlow(
                    portfolio_id=portfolio.id,
                    kind="DEPOSIT",
                    amount=Decimal("25000"),
                    occurred_at=panel.index[-5].to_pydatetime(),
                )
            )
            for g in persona.goals:
                s.add(
                    Goal(
                        customer_id=customer.id,
                        portfolio_id=portfolio.id,
                        **{
                            k: (
                                Decimal(str(v))
                                if k
                                in {"target_amount_real", "initial_amount", "monthly_contribution"}
                                else v
                            )
                            for k, v in g.items()
                        },
                    )
                )
            await s.commit()
            pid = portfolio.id
        outcome = await container.proposals.create(
            pid, actor="system", source="scheduler", trigger="drift"
        )
        if outcome.proposal is not None:
            created.append(outcome.proposal.id)
    logger.info("demo_seeded", personas=len(PERSONAS), proposals=len(created))
    return {
        "seeded": True,
        "users": [p.username for p in PERSONAS] + [ADVISOR_USER[0]],
        "proposals": created,
    }


__all__ = ["ADVISOR_USER", "DEMO_PW", "PERSONAS", "persona_answers", "seed_demo"]
