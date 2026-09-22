"""Pydantic v2 schemas for the Transaction resource."""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Side(str, Enum):
    """Direction of a trade: BUY acquires, SELL disposes."""

    BUY = "BUY"
    SELL = "SELL"


class TransactionCreate(BaseModel):
    """Payload for placing a manual transaction via the CRUD API."""

    portfolio_id: int = Field(gt=0)
    ticker: str = Field(min_length=1, max_length=16)
    side: Side
    quantity: float = Field(gt=0.0)
    price: float = Field(gt=0.0)
    reason: str = Field(default="manual", max_length=32)

    @field_validator("side", mode="before")
    @classmethod
    def _upper_side(cls, value: object) -> object:
        if isinstance(value, str):
            return value.upper()
        return value


class TransactionRead(BaseModel):
    """API representation of a ledger transaction."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    portfolio_id: int
    ticker: str
    side: Side  # type: ignore[assignment]
    quantity: float
    price: float
    total_amount: float
    reason: str
    executed_at: datetime
