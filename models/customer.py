"""Customer (Müşteri) ORM model.

Represents a bank customer whose investment profile feeds the Risk Agent.
The static inputs stored here (investment horizon, income, self-declared
tolerance) are the *inputs* to the dynamic risk score computation, never the
score itself — the Risk Agent computes the dynamic score at runtime.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base

if TYPE_CHECKING:
    from models.portfolio import Portfolio


def _utcnow() -> datetime:
    """Return timezone-aware UTC now (stored without tz by SQLite)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Customer(Base):
    """ORM model of the ``customers`` table."""

    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)

    # --- Yatırımcı profili (Risk Ajanı girdileri) ---------------------------
    investment_horizon_years: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    monthly_income: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # Kullanıcı beyanı risk toleransı: 1 (çok muhafazakâr) – 5 (agresif)
    declared_risk_tolerance: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    financial_goal: Mapped[str] = mapped_column(Text, nullable=True)

    # --- Zaman damgaları -----------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, default=_utcnow, onupdate=_utcnow
    )

    # --- İlişkiler ------------------------------------------------------------
    portfolios: Mapped[list["Portfolio"]] = relationship(
        back_populates="customer",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Customer id={self.id} email={self.email!r}>"
