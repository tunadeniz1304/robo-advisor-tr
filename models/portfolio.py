"""Portfolio (Portföy) ORM model.

A portfolio belongs to exactly one customer and holds:

    * ``cash`` — available nakit (TL); the buying power for rebalancing.
    * ``holdings`` — current positions as a JSON map ``{ticker: quantity}``.

Rebalancing performed by the Portfolio Manager updates ``holdings`` (SQL
UPDATE) and records one row per side-change into the ``transactions`` table
(SQL INSERT), keeping the ledger consistent with the realized positions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base

if TYPE_CHECKING:
    from models.customer import Customer
    from models.transaction import Transaction


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


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
    cash: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Varlık dağılımı: {ticker: quantity} — JSON sütunu.
    holdings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow, onupdate=_utcnow)

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
