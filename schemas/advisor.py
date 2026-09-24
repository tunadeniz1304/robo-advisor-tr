"""Pydantic schemas for the Advisor (rebalancing) API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AdvisorResponse(BaseModel):
    """Payload returned by the advisor rebalancing endpoint."""

    customer_id: int
    portfolio_id: int
    weights: dict[str, float] = Field(default_factory=dict)
    orders: list[dict[str, Any]] = Field(default_factory=list)
    report: str = ""
    error: str | None = None
    llm_mode: str | None = None
    llm_error_kind: str | None = None
    run_id: str | None = None
