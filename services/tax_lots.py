"""Tax-lot bookkeeping (FIFO / HIFO) and read helpers.

``HIFO`` (highest cost first) minimises realised gains — and therefore
withholding tax — when selling; ``FIFO`` is the regulatory default for many
products. The chosen method is configured in the investment policy.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.money import to_decimal, to_qty
from models.base import utcnow
from models.tax_lot import TaxLot


async def add_lot(
    session: AsyncSession,
    portfolio_id: int,
    symbol: str,
    quantity: Decimal,
    price: Decimal,
    fees: Decimal = Decimal("0"),
    acquired_at: datetime | None = None,
) -> TaxLot:
    """Open a new lot for a buy; fees are capitalised into the unit cost."""
    unit_cost = to_decimal(price + (fees / quantity if quantity > 0 else Decimal("0")))
    lot = TaxLot(
        portfolio_id=portfolio_id,
        symbol=symbol,
        quantity_open=to_qty(quantity),
        quantity_initial=to_qty(quantity),
        unit_cost=unit_cost,
        acquired_at=acquired_at or utcnow(),
    )
    session.add(lot)
    return lot


async def open_lots(
    session: AsyncSession, portfolio_id: int, symbol: str | None = None
) -> list[TaxLot]:
    """Return open lots (oldest first) of a portfolio, optionally for one symbol."""
    stmt = select(TaxLot).where(TaxLot.portfolio_id == portfolio_id)
    if symbol is not None:
        stmt = stmt.where(TaxLot.symbol == symbol)
    stmt = stmt.order_by(TaxLot.acquired_at, TaxLot.id)
    lots = (await session.execute(stmt)).scalars().all()
    return [lot for lot in lots if lot.quantity_open > 0]


def order_lots(lots: list[Any], method: str) -> list[Any]:
    """Order lots for consumption (FIFO: oldest first, HIFO: highest cost first)."""
    if method.upper() == "HIFO":
        return sorted(lots, key=lambda lot: (-float(lot.unit_cost), lot.acquired_at))
    return sorted(lots, key=lambda lot: (lot.acquired_at, getattr(lot, "id", 0) or 0))


async def consume_lots(
    session: AsyncSession,
    portfolio_id: int,
    symbol: str,
    quantity: Decimal,
    price: Decimal,
    method: str = "FIFO",
    fees: Decimal = Decimal("0"),
) -> tuple[Decimal, list[dict[str, Any]]]:
    """Consume open lots for a sell and return ``(realised_pnl, consumed)``.

    The sale's costs (commission + BSMV) are allocated pro rata to the lots
    consumed, so the realised gain is net of them.

    Positions opened before lot tracking (no lots) are treated as having a
    cost equal to the sale price (zero realised gain).
    """
    remaining = to_qty(quantity)
    cost_per_unit = fees / remaining if remaining > 0 else Decimal("0")
    realized = Decimal("0")
    consumed: list[dict[str, Any]] = []
    for lot in order_lots(await open_lots(session, portfolio_id, symbol), method):
        if remaining <= 0:
            break
        take = min(remaining, lot.quantity_open)
        lot.quantity_open = to_qty(lot.quantity_open - take)
        gain = to_decimal((price - lot.unit_cost - cost_per_unit) * take)
        realized += gain
        consumed.append(
            {
                "lot_id": lot.id,
                "quantity": float(take),
                "unit_cost": float(lot.unit_cost),
                "acquired_at": lot.acquired_at.isoformat(),
                "gain": float(gain),
            }
        )
        remaining -= take
    return to_decimal(realized), consumed


async def average_cost_basis(session: AsyncSession, portfolio_id: int) -> dict[str, float]:
    """Weighted average unit cost of open lots per symbol."""
    totals: dict[str, tuple[Decimal, Decimal]] = {}
    for lot in await open_lots(session, portfolio_id):
        qty, cost = totals.get(lot.symbol, (Decimal("0"), Decimal("0")))
        totals[lot.symbol] = (qty + lot.quantity_open, cost + lot.quantity_open * lot.unit_cost)
    return {s: float(c / q) for s, (q, c) in totals.items() if q > 0}


__all__ = ["add_lot", "average_cost_basis", "consume_lots", "open_lots", "order_lots"]
