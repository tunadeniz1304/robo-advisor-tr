"""Idempotent seeding of reference data: instruments and model portfolios.

Runs on every startup after migrations. Instruments are upserted by symbol;
model portfolios are versioned by the policy version — a new policy version
adds new rows and deactivates the previous ones (history is kept).
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select, update

from core.database import session_factory
from core.logging import get_logger
from core.policy import InvestmentPolicy, get_policy
from models import Instrument, ModelPortfolio
from services.market_data.universe import UNIVERSE

logger = get_logger("otonom.reference")


async def seed_instruments() -> int:
    """Insert or update every instrument of the universe."""
    changed = 0
    async with session_factory() as session:
        existing = {
            row.symbol: row for row in (await session.execute(select(Instrument))).scalars().all()
        }
        for spec in UNIVERSE:
            row = existing.get(spec.symbol)
            if row is None:
                row = Instrument(symbol=spec.symbol)
                session.add(row)
                changed += 1
            row.name = spec.name
            row.asset_class = spec.asset_class
            row.currency = spec.currency
            row.source = spec.source
            row.yahoo_symbol = spec.yahoo_symbol
            row.risk_score = spec.risk_score
            row.expense_ratio = spec.expense_ratio
            row.liquidity_days = spec.liquidity_days
            row.min_trade_amount = Decimal(str(spec.min_trade_amount))
            row.sector = spec.sector
            row.esg_member = spec.esg_member
            row.esg_score = spec.esg_score
            row.description = spec.description
            row.is_active = True
        await session.commit()
    return changed


async def seed_model_portfolios(policy: InvestmentPolicy | None = None) -> int:
    """Create the model portfolio library for the current policy version."""
    policy = policy or get_policy()
    created = 0
    async with session_factory() as session:
        current = (
            (
                await session.execute(
                    select(ModelPortfolio).where(ModelPortfolio.version == policy.version)
                )
            )
            .scalars()
            .all()
        )
        if len(current) >= len(policy.model_portfolios):
            return 0
        await session.execute(
            update(ModelPortfolio)
            .where(ModelPortfolio.version != policy.version)
            .values(is_active=False)
        )
        have = {row.level for row in current}
        for level, weights in sorted(policy.model_portfolios.items()):
            if level in have:
                continue
            session.add(
                ModelPortfolio(
                    level=level,
                    version=policy.version,
                    name=f"Model {level} — {policy.risk_label(level)}",
                    class_weights=weights,
                    is_active=True,
                )
            )
            created += 1
        await session.commit()
    return created


async def seed_reference_data() -> dict[str, int]:
    """Seed instruments and model portfolios (idempotent)."""
    result = {
        "instruments": await seed_instruments(),
        "model_portfolios": await seed_model_portfolios(),
    }
    logger.info("reference_data_seeded", **result)
    return result


__all__ = ["seed_instruments", "seed_model_portfolios", "seed_reference_data"]
