"""Pydantic v2 schemas for the Portfolio resource."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from core.money import SUPPORTED_CURRENCIES


class PortfolioCreate(BaseModel):
    """Payload for creating a portfolio."""

    customer_id: int = Field(gt=0)
    name: str = Field(min_length=2, max_length=160, examples=["Ana Portföy"])
    currency: str = Field(default="TRY", pattern="^(" + "|".join(SUPPORTED_CURRENCIES) + ")$")
    cash: float = Field(default=0.0, ge=0.0)
    holdings: dict[str, float] = Field(default_factory=dict)


class PortfolioUpdate(BaseModel):
    """Payload for updating portfolio metadata.

    ``cash``/``holdings`` edits are an administrative correction (staff only);
    regular changes go through transactions and approved proposals.
    """

    name: str | None = Field(default=None, min_length=2, max_length=160)
    currency: str | None = Field(default=None, pattern="^(" + "|".join(SUPPORTED_CURRENCIES) + ")$")
    cash: float | None = Field(default=None, ge=0.0)
    holdings: dict[str, float] | None = None


class PortfolioRead(BaseModel):
    """API representation of a portfolio."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    name: str
    currency: str
    cash: Decimal
    holdings: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @field_serializer("cash")
    def _money(self, value: Decimal) -> float:
        return round(float(value), 2)
