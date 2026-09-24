"""Execution domain: rebalance proposals (state machine), orders, fills,
cash flows and autopilot settings."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base
from core.money import Money, Quantity
from models.base import utcnow

# Öneri durum makinesi
PROPOSAL_DRAFT = "TASLAK"
PROPOSAL_PENDING = "ONAY_BEKLIYOR"
PROPOSAL_APPROVED = "ONAYLANDI"
PROPOSAL_EXECUTED = "YURUTULDU"
PROPOSAL_REJECTED = "REDDEDILDI"
PROPOSAL_EXPIRED = "SURESI_DOLDU"
PROPOSAL_FAILED = "BASARISIZ"

PROPOSAL_TRANSITIONS: dict[str, frozenset[str]] = {
    PROPOSAL_DRAFT: frozenset({PROPOSAL_PENDING, PROPOSAL_REJECTED, PROPOSAL_EXPIRED}),
    PROPOSAL_PENDING: frozenset({PROPOSAL_APPROVED, PROPOSAL_REJECTED, PROPOSAL_EXPIRED}),
    PROPOSAL_APPROVED: frozenset({PROPOSAL_EXECUTED, PROPOSAL_FAILED, PROPOSAL_EXPIRED}),
    PROPOSAL_EXECUTED: frozenset(),
    PROPOSAL_REJECTED: frozenset(),
    PROPOSAL_EXPIRED: frozenset(),
    PROPOSAL_FAILED: frozenset(),
}
OPEN_PROPOSAL_STATES = (PROPOSAL_DRAFT, PROPOSAL_PENDING, PROPOSAL_APPROVED)


class RebalanceProposal(Base):
    """A rebalance proposal: draft → pending approval → approved → executed."""

    __tablename__ = "rebalance_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("portfolios.id", ondelete="CASCADE"), index=True, nullable=False
    )
    customer_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), index=True, nullable=False, default=PROPOSAL_DRAFT
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="manual"
    )  # manual|scheduler|copilot|autopilot
    trigger: Mapped[str] = mapped_column(
        String(16), nullable=False, default="manual"
    )  # manual|drift|calendar|cash
    model_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_weights: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    before_weights: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    after_weights: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    orders: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    prices: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    estimated_cost: Mapped[Decimal] = mapped_column(Money(), nullable=False, default=Decimal("0"))
    estimated_tax: Mapped[Decimal] = mapped_column(Money(), nullable=False, default=Decimal("0"))
    turnover: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    risk_before: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    risk_after: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    optimizer: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    explanation: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    suitability: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    llm_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    graph_thread_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approval_idempotency_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    rejected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    execution_report: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow, onupdate=utcnow)


class Order(Base):
    """An order generated from an approved proposal."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proposal_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("rebalance_proposals.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    portfolio_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("portfolios.id", ondelete="CASCADE"), index=True, nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(24), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Quantity(), nullable=False)
    reference_price: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="NEW"
    )  # NEW|FILLED|REJECTED
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)


class Fill(Base):
    """Execution report of an order (simulated broker)."""

    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("orders.id", ondelete="CASCADE"), index=True, nullable=False
    )
    transaction_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True
    )
    quantity: Mapped[Decimal] = mapped_column(Quantity(), nullable=False)
    price: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    fees: Mapped[Decimal] = mapped_column(Money(), nullable=False, default=Decimal("0"))
    tax: Mapped[Decimal] = mapped_column(Money(), nullable=False, default=Decimal("0"))
    slippage_bps: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    executed_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)


class CashFlow(Base):
    """Deposits, withdrawals, dividends and fees (for money-weighted returns)."""

    __tablename__ = "cash_flows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("portfolios.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # DEPOSIT|WITHDRAWAL|DIVIDEND|FEE
    amount: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="TRY")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow, index=True)


class AutopilotSetting(Base):
    """Per-portfolio cash sweep (Autopilot) configuration."""

    __tablename__ = "autopilot_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("portfolios.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cash_threshold: Mapped[Decimal] = mapped_column(
        Money(), nullable=False, default=Decimal("5000")
    )
    target_symbol: Mapped[str] = mapped_column(String(24), nullable=False, default="TL_PPF")
    # oneri: yalnızca öneri üretir | otomatik: önceden verilmiş onayla yürütür
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="oneri")
    updated_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow, onupdate=utcnow)
