"""Advice domain tables: risk profiles, model portfolios, goals and BL views."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base
from core.money import Money
from models.base import utcnow


class RiskProfile(Base):
    """Versioned SPK suitability questionnaire result of a customer."""

    __tablename__ = "risk_profiles"
    __table_args__ = (UniqueConstraint("customer_id", "version", name="uq_risk_profile_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("customers.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    questionnaire_version: Mapped[str] = mapped_column(String(16), nullable=False)
    answers: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    capacity_score: Mapped[float] = mapped_column(Float, nullable=False)
    tolerance_score: Mapped[float] = mapped_column(Float, nullable=False)
    knowledge_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    capacity_level: Mapped[int] = mapped_column(Integer, nullable=False)
    tolerance_level: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_level: Mapped[int] = mapped_column(Integer, nullable=False)
    warnings: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    explanation: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    valid_until: Mapped[datetime] = mapped_column(nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)


class ModelPortfolio(Base):
    """Versioned model portfolio (asset-class weights) for a risk level."""

    __tablename__ = "model_portfolios"
    __table_args__ = (UniqueConstraint("level", "version", name="uq_model_portfolio_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    version: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    class_weights: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    expected_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_volatility: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)


class Goal(Base):
    """A customer goal (Betterment style bucket or share of the portfolio)."""

    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("customers.id", ondelete="CASCADE"), index=True, nullable=False
    )
    portfolio_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("portfolios.id", ondelete="SET NULL"), nullable=True
    )
    goal_type: Mapped[str] = mapped_column(
        String(24), nullable=False
    )  # emeklilik|ev|egitim|acil_durum|diger
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    target_amount_real: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    horizon_years: Mapped[float] = mapped_column(Float, nullable=False)
    initial_amount: Mapped[Decimal] = mapped_column(Money(), nullable=False, default=Decimal("0"))
    monthly_contribution: Mapped[Decimal] = mapped_column(
        Money(), nullable=False, default=Decimal("0")
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Ana portföyün bu hedefe ayrılan payı (kova); None → bağımsız kova
    portfolio_share: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_simulation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow, onupdate=utcnow)


class BLView(Base):
    """Black-Litterman house view (entered by staff or suggested by the LLM)."""

    __tablename__ = "bl_views"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(24), index=True, nullable=False)
    expected_return: Mapped[float] = mapped_column(Float, nullable=False)  # yıllık, TL bazında
    confidence: Mapped[float] = mapped_column(Float, nullable=False)  # 0–1 (Idzorek)
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="danisman"
    )  # danisman|llm
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="onerildi")
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)
    valid_until: Mapped[datetime | None] = mapped_column(nullable=True)
