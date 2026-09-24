"""Pydantic v2 schemas for the Transaction resource."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


class Side(StrEnum):
    """Direction of a trade: BUY acquires, SELL disposes."""

    BUY = "BUY"
    SELL = "SELL"


class TransactionCreate(BaseModel):
    """Payload for placing a manual transaction via the API."""

    portfolio_id: int = Field(gt=0)
    ticker: str = Field(min_length=1, max_length=24)
    side: Side
    quantity: float = Field(gt=0.0)
    price: float = Field(gt=0.0)
    fees: float = Field(default=0.0, ge=0.0)
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
    side: Side
    quantity: Decimal
    price: Decimal
    total_amount: Decimal
    fees: Decimal = Decimal("0")
    tax: Decimal = Decimal("0")
    currency: str = "TRY"
    reason: str
    executed_at: datetime

    @field_serializer("quantity")
    def _qty(self, value: Decimal) -> float:
        return float(value)

    @field_serializer("price", "total_amount", "fees", "tax")
    def _money(self, value: Decimal) -> float:
        return round(float(value), 6)
