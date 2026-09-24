"""Risk Ajanı (Risk Agent) — LangGraph node.

Loads the customer profile from the database inside the node (a fresh async
session is opened for the run — the graph remains stateless w.r.t. sessions)
and computes the *dynamic* risk score via :class:`services.risk_service.
RiskService`. The result — score, category and maximum equity weight — is
written back to the graph state for the Portfolio Manager.

A missing customer is a fatal, explicit error (the graph cannot proceed
without a profile), reported through ``state["error"]``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from agents.state import AdvisorState
from core.database import session_factory
from core.logging import get_logger
from models import Customer
from services.risk_service import RiskService

logger = get_logger("otonom.agent.risk")


class RiskAgent:
    """Node factory; resolves customers from the DB and scores risk."""

    def __init__(self, risk_service: RiskService) -> None:
        self._risk = risk_service

    def node(self) -> Callable[[AdvisorState], Awaitable[dict]]:
        """Return the async node function runnable by LangGraph."""

        async def run(state: AdvisorState) -> dict:
            customer_id = state.get("customer_id")
            if not customer_id:
                return {"error": "customer_id eksik: risk değerlendirmesi yapılamaz."}

            async with session_factory() as session:  # type: AsyncSession
                customer = await session.get(Customer, customer_id)
                if customer is None:
                    logger.warning("risk_agent_customer_missing", customer_id=customer_id)
                    return {
                        "risk": {},
                        "error": f"Customer {customer_id} bulunamadı: risk skoru hesaplanamadı.",
                    }

                assessment = self._risk.assess(customer)
                logger.info(
                    "risk_agent_completed",
                    customer_id=customer_id,
                    score=round(assessment.score, 2),
                    category=assessment.category,
                )
                return {
                    "risk": assessment.to_dict(),
                    "error": None,
                }

        return run
