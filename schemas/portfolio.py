"""Pydantic v2 schemas for the Portfolio resource."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PortfolioCreate(BaseModel):
    """Payload for creating a portfolio."""

    customer_id: int = Field(gt=0)
    name: str = Field(min_length=2, max_length=160, examples=["Ana Portföy"])
    currency: str = Field(default="TRY", min_length=3, max_length=8)
    cash: float = Field(default=0.0, ge=0.0)
    holdings: dict[str, Any] = Field(default_factory=dict)


class PortfolioUpdate(BaseModel):
    """Payload for updating portfolio metadata (holdings/cash are managed by
    the advisor rebalancing, but exposing them here keeps the CRUD complete)."""

    name: str | None = Field(default=None, min_length=2, max_length=160)
    currency: str | None = Field(default=None, min_length=3, max_length=8)
    cash: float | None = Field(default=None, ge=0.0)
    holdings: dict[str, Any] | None = None


class PortfolioRead(BaseModel):
    """API representation of a portfolio."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    name: str
    currency: str
    cash: float
    holdings: dict[str, Any]
    created_at: datetime
    updated_at: datetime
