"""LLM usage log — one row per model call (no prompt/response content)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base
from models.base import utcnow


class LLMUsage(Base):
    """ORM model of the ``llm_usage`` table."""

    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow, index=True)
    purpose: Mapped[str] = mapped_column(String(48), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)  # live | demo | fallback
    model: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    error_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
