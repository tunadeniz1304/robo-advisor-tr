"""Uygunluk/Risk Ajanı — LangGraph node.

Resolves the customer's effective risk level: the valid SPK suitability
profile when present, otherwise a legacy estimate from the dynamic risk
score (flagged ``source="tahmini"`` so the UI asks for the questionnaire).
Expired profiles are reported so the proposal can warn about renewal.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from agents.state import RebalanceState
from core.database import session_factory
from core.logging import get_logger
from models import Customer
from services.suitability.service import effective_level

logger = get_logger("otonom.agent.risk")


class RiskAgent:
    """Node factory for the suitability step."""

    def node(self) -> Callable[[RebalanceState], Awaitable[dict[str, Any]]]:
        async def run(state: RebalanceState) -> dict[str, Any]:
            customer_id = state.get("customer_id")
            if not customer_id:
                return {"error": "customer_id eksik: risk değerlendirmesi yapılamaz."}
            async with session_factory() as session:
                customer = await session.get(Customer, int(customer_id))
                if customer is None:
                    return {
                        "error": f"Customer {customer_id} bulunamadı: risk seviyesi hesaplanamadı."
                    }
                level = await effective_level(session, customer)
            logger.info(
                "risk_agent_completed",
                customer_id=customer_id,
                level=level.level,
                source=level.source,
            )
            return {"level": level.to_dict(), "error": None}

        return run


__all__ = ["RiskAgent"]
