"""Transaction (İşlem) ORM model — the immutable trade ledger.

Every trade executed on a portfolio (manual, rebalance fill, cash sweep) is a
row here. ``side`` is ``BUY`` / ``SELL``; amounts are exact decimals.

Taxonomy of ``reason``: ``manual``, ``rebalance``, ``sweep``, ``seed``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base
from core.money import Money, Quantity
from models.base import utcnow

if TYPE_CHECKING:
    from models.portfolio import Portfolio


class Transaction(Base):
    """ORM model of the ``transactions`` table."""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("portfolios.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    ticker: Mapped[str] = mapped_column(String(24), index=True, nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)  # BUY | SELL
    quantity: Mapped[Decimal] = mapped_column(Quantity(), nullable=False)
    price: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    # quantity * price, denormalized for fast reporting.
    total_amount: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    fees: Mapped[Decimal] = mapped_column(Money(), nullable=False, default=Decimal("0"))
    tax: Mapped[Decimal] = mapped_column(Money(), nullable=False, default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="TRY")
    reason: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    executed_at: Mapped[datetime] = mapped_column(index=True, default=utcnow)

    # --- İlişkiler ------------------------------------------------------------
    portfolio: Mapped[Portfolio] = relationship(back_populates="transactions")

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"<Transaction id={self.id} portfolio_id={self.portfolio_id} "
            f"{self.side} {self.ticker} x{self.quantity} @ {self.price}>"
        )
