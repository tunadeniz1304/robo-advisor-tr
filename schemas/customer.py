"""Pydantic v2 schemas for the Customer resource."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# --- Create ----------------------------------------------------------------


class CustomerCreate(BaseModel):
    """Payload for creating a customer."""

    full_name: str = Field(min_length=2, max_length=160, examples=["Ayşe Yılmaz"])
    email: EmailStr = Field(examples=["ayse.yilmaz@example.com"])
    investment_horizon_years: int = Field(default=5, ge=1, le=50)
    monthly_income: float = Field(default=0.0, ge=0.0)
    declared_risk_tolerance: int = Field(default=3, ge=1, le=5)
    financial_goal: str | None = Field(default=None, max_length=500)


# --- Update ----------------------------------------------------------------


class CustomerUpdate(BaseModel):
    """Payload for updating an existing customer (all fields optional)."""

    full_name: str | None = Field(default=None, min_length=2, max_length=160)
    email: EmailStr | None = None
    investment_horizon_years: int | None = Field(default=None, ge=1, le=50)
    monthly_income: float | None = Field(default=None, ge=0.0)
    declared_risk_tolerance: int | None = Field(default=None, ge=1, le=5)
    financial_goal: str | None = Field(default=None, max_length=500)


# --- Read ------------------------------------------------------------------


class CustomerRead(BaseModel):
    """API representation of a customer (ORM->schema)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    email: str
    investment_horizon_years: int
    monthly_income: float
    declared_risk_tolerance: int
    financial_goal: str | None
    created_at: datetime
    updated_at: datetime
