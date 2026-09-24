"""Advisor runs audit trail — every agent decision persisted for regulators.

Regulatory best practice (ESMA MiFID II suitability guidelines, EU AI Act
transparency) is that algorithmic investment advice leaves a *complete,
queryable audit trail*: which system decided what, based on which inputs, and
with which rationale. This model persists one row per advisory run:

    * the portfolio & customer,
    * the input market snapshot (JSON),
    * the output target weights and orders (JSON),
    * the LLM narrative (if produced),
    * timestamps and overall status.

It is write-only from the workflow and read-only via the API (routers/
runs.py). It deliberately stores JSON copies rather than live references so
the audit record is immutable even if holdings later change.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class AdvisorRun(Base):
    """ORM model of the ``advisor_runs`` audit trail table."""

    __tablename__ = "advisor_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("portfolios.id", ondelete="CASCADE"), index=True, nullable=False
    )
    customer_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    # Snapshot of the inputs the agents saw.
    market_input: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    # Outputs.
    target_weights: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    orders: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    report: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="success")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Timestamps.
    created_at: Mapped[datetime] = mapped_column(index=True, default=_utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<AdvisorRun id={self.id} portfolio_id={self.portfolio_id} status={self.status}>"
