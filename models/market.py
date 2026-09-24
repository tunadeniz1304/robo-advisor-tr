"""Reference and market data tables: instruments, prices, macro series."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base
from core.money import Money
from models.base import utcnow


class Instrument(Base):
    """Investable instrument of the multi-asset universe."""

    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(24), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="TRY")
    # yfinance | proxy | tefas | evds
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="yfinance")
    yahoo_symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tefas_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # SRRI benzeri risk göstergesi (1–7)
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    # Yıllık yönetim ücreti (kesir)
    expense_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # Likidite: gün cinsinden nakde dönüş süresi
    liquidity_days: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    min_trade_amount: Mapped[Decimal] = mapped_column(Money(), nullable=False, default=Decimal("0"))
    sector: Mapped[str | None] = mapped_column(String(48), nullable=True)
    esg_member: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    esg_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)


class PriceHistory(Base):
    """Daily close per instrument (persisted from live downloads)."""

    __tablename__ = "price_history"

    symbol: Mapped[str] = mapped_column(String(24), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="live")


class MacroSeries(Base):
    """Monthly macro observations (TÜFE index, policy rate, USDTRY …)."""

    __tablename__ = "macro_series"

    series: Mapped[str] = mapped_column(String(32), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="snapshot")
