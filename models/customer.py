"""Customer (Müşteri) ORM model.

Represents a bank customer whose investment profile feeds the suitability and
risk services. Personal data is protected at rest (KVKK):

    * ``email`` is Fernet-encrypted; ``email_hash`` (keyed HMAC) keeps it
      unique and searchable without clear text.
    * ``monthly_income`` is Fernet-encrypted.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from core.crypto import EncryptedDecimal, EncryptedString, blind_index
from core.database import Base
from models.base import utcnow

if TYPE_CHECKING:
    from models.portfolio import Portfolio


class Customer(Base):
    """ORM model of the ``customers`` table."""

    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    email_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)

    # --- Yatırımcı profili (uygunluk/risk girdileri) -----------------------
    investment_horizon_years: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    monthly_income: Mapped[Decimal] = mapped_column(
        EncryptedDecimal(), nullable=False, default=Decimal("0")
    )
    # Kullanıcı beyanı risk toleransı: 1 (çok muhafazakâr) – 5 (agresif)
    declared_risk_tolerance: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    financial_goal: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Atanmış danışman (users.id); danışman yalnızca kendi müşterilerini görür.
    advisor_user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL", use_alter=True, name="fk_customers_advisor"),
        nullable=True,
        index=True,
    )

    # --- Zaman damgaları -----------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow, onupdate=utcnow)

    # --- İlişkiler ------------------------------------------------------------
    portfolios: Mapped[list[Portfolio]] = relationship(
        back_populates="customer",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @validates("email")
    def _sync_email_hash(self, _key: str, value: str) -> str:
        """Keep the blind index in sync whenever the e-mail changes."""
        normalised = value.strip().lower()
        self.email_hash = blind_index(normalised)
        return normalised

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Customer id={self.id}>"
