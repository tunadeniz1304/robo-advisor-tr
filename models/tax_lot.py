"""Tax lots — per-purchase cost basis used for tax-aware selling."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base
from core.money import Money, Quantity
from models.base import utcnow


class TaxLot(Base):
    """ORM model of the ``tax_lots`` table (one row per buy)."""

    __tablename__ = "tax_lots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("portfolios.id", ondelete="CASCADE"), index=True, nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(24), index=True, nullable=False)
    quantity_open: Mapped[Decimal] = mapped_column(Quantity(), nullable=False)
    quantity_initial: Mapped[Decimal] = mapped_column(Quantity(), nullable=False)
    # Birim maliyet (alış fiyatı + dağıtılmış işlem maliyeti).
    unit_cost: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow, index=True)
