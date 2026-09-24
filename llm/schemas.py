"""Pydantic schemas every LLM output is validated against.

Each task of :mod:`llm.prompts` maps to exactly one schema. Numeric fields are
deliberately rare: numbers belong to the deterministic services, the model
only writes prose around them (see :mod:`llm.guard`).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RebalanceRationale(BaseModel):
    """Why the proposed rebalance makes sense."""

    ozet: str = Field(min_length=10, max_length=1200)
    gerekceler: list[str] = Field(default_factory=list, max_length=6)
    riskler: list[str] = Field(default_factory=list, max_length=4)


class MarketCommentary(BaseModel):
    """Grounded market/macro commentary (Aladdin style auto commentary)."""

    baslik: str = Field(min_length=3, max_length=160)
    ozet: str = Field(min_length=10, max_length=1500)
    maddeler: list[str] = Field(default_factory=list, max_length=8)


class GoalReport(BaseModel):
    """Plain-language report on a goal simulation."""

    ozet: str = Field(min_length=10, max_length=1200)
    oneriler: list[str] = Field(default_factory=list, max_length=5)


class AllocationExplanation(BaseModel):
    """Fluent rendering of the deterministic "why this allocation" cards."""

    ozet: str = Field(min_length=10, max_length=1500)
    maddeler: list[str] = Field(default_factory=list, max_length=8)


class PortfolioLetter(BaseModel):
    """Weekly portfolio letter."""

    baslik: str = Field(min_length=3, max_length=160)
    giris: str = Field(min_length=10, max_length=1200)
    performans: str = Field(min_length=10, max_length=1200)
    risk: str = Field(min_length=10, max_length=1200)
    gorunum: str = Field(min_length=10, max_length=1200)
    sonuc: str = Field(min_length=10, max_length=800)


class ViewSuggestion(BaseModel):
    """A Black-Litterman view *suggested* by the LLM (human approval needed)."""

    varlik: str = Field(min_length=1, max_length=32)
    beklenen_getiri: float = Field(ge=-0.9, le=3.0)
    guven: float = Field(ge=0.05, le=0.95)
    gerekce: str = Field(min_length=5, max_length=400)


class ViewSuggestions(BaseModel):
    """Container for suggested views."""

    gorusler: list[ViewSuggestion] = Field(default_factory=list, max_length=6)


class CopilotAnswer(BaseModel):
    """Final copilot answer."""

    yanit: str = Field(min_length=2, max_length=4000)


__all__ = [
    "AllocationExplanation",
    "CopilotAnswer",
    "GoalReport",
    "MarketCommentary",
    "PortfolioLetter",
    "RebalanceRationale",
    "ViewSuggestion",
    "ViewSuggestions",
]
