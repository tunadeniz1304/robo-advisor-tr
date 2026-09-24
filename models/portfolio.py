"""Portfolio (Portföy) ORM model.

A portfolio belongs to exactly one customer and holds:

    * ``cash`` — available cash in ``currency`` (exact decimal).
    * ``holdings`` — current positions as a JSON map ``{symbol: quantity}``.

All mutations of ``cash``/``holdings`` go through the ledger service so that
positions, cash, tax lots and the transaction ledger stay consistent.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base
from core.money import Money
from models.base import utcnow

if TYPE_CHECKING:
    from models.customer import Customer
    from models.transaction import Transaction


class Portfolio(Base):
    """ORM model of the ``portfolios`` table."""

    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("customers.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="TRY")
    cash: Mapped[Decimal] = mapped_column(Money(), nullable=False, default=Decimal("0"))

    # Varlık dağılımı: {symbol: quantity} — JSON sütunu.
    holdings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow, onupdate=utcnow)

    # --- İlişkiler ------------------------------------------------------------
    customer: Mapped[Customer] = relationship(back_populates="portfolios")
    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Transaction.executed_at",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Portfolio id={self.id} customer_id={self.customer_id} name={self.name!r}>"
