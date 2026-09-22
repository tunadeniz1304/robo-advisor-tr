"""Advisor service — orchestrates a Robo-Advisor run end-to-end.

Responsibilities:
    1. Load the target portfolio and its customer from the database.
    2. Assemble the LangGraph workflow with injected real services
       (market, risk, MPT, LLM).
    3. Run the graph and return a structured :class:`AdvisorResult` that the
       API serialises back to the client (weights, orders, LLM report).

The service intentionally owns *no* secrets and never imports model classes
into the API layer directly; it is the seam between HTTP and the graph.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select

from agents.graph import AdvisorGraph, build_advisor_graph
from core.config import Settings
from core.database import session_factory
from core.logging import get_logger
from llm.clients import LLMClient, LLMConfigurationError, get_llm_client
from models import Customer, Portfolio
from services.market_service import MarketService
from services.portfolio_service import PortfolioService
from services.risk_service import RiskService

logger = get_logger("otonom.advisor")


@dataclass
class AdvisorResult:
    """Structured outcome of a rebalancing run."""

    customer_id: int
    portfolio_id: int
    weights: dict[str, float] = field(default_factory=dict)
    orders: list[dict[str, object]] = field(default_factory=list)
    report: str = ""
    error: Optional[str] = None

    def to_dict(self) -> dict[str, object]:
        return {
            "customer_id": self.customer_id,
            "portfolio_id": self.portfolio_id,
            "weights": self.weights,
            "orders": self.orders,
            "report": self.report,
            "error": self.error,
        }


class AdvisorService:
    """Runs the multi-agent rebalancing workflow for one portfolio."""

    def __init__(
        self,
        settings: Settings,
        market_service: Optional[MarketService] = None,
        risk_service: Optional[RiskService] = None,
        portfolio_service: Optional[PortfolioService] = None,
        llm_client: Optional[LLMClient] = None,
        checkpoint_db: Optional[str] = None,
    ) -> None:
        self._settings = settings
        # Real services by default; overridable for tests via DI.
        from services.market_service import YFinanceSource

        self._market = market_service or MarketService(YFinanceSource())
        self._risk = risk_service or RiskService()
        self._mpt = portfolio_service or PortfolioService()
        self._llm = llm_client
        self._checkpoint_db = checkpoint_db

    def _resolve_llm(self) -> LLMClient:
        """Build the real LLM client from settings (or raise clearly)."""
        if self._llm is None:
            self._llm = get_llm_client(self._settings)
        return self._llm

    async def run_rebalance(self, portfolio_id: int, customer_id: int) -> AdvisorResult:
        """Execute the LangGraph workflow for a portfolio.

        Args:
            portfolio_id: Portfolio to rebalance (must exist in DB).
            customer_id: Owner of the portfolio (must match DB).

        Returns:
            :class:`AdvisorResult` with weights/orders/report or an error.
        """
        try:
            llm = self._resolve_llm()
        except LLMConfigurationError as exc:
            logger.warning("advisor_llm_missing", error=str(exc))
            return AdvisorResult(
                customer_id=customer_id,
                portfolio_id=portfolio_id,
                error=str(exc),
            )

        async with session_factory() as session:
            portfolio = await session.get(Portfolio, portfolio_id)
            if portfolio is None or portfolio.customer_id != customer_id:
                return AdvisorResult(
                    customer_id=customer_id,
                    portfolio_id=portfolio_id,
                    error=(
                        f"Portfolio {portfolio_id} customer {customer_id} için bulunamadı "
                        "veya müşteriye ait değil."
                    ),
                )
            holdings = dict(portfolio.holdings or {})

        thread_id = f"portfolio-{portfolio_id}"
        graph: AdvisorGraph = build_advisor_graph(
            market_service=self._market,
            risk_service=self._risk,
            portfolio_service=self._mpt,
            llm_client=llm,
            thread_id=thread_id,
            checkpoint_db=self._checkpoint_db,
        )

        initial_state = {
            "customer_id": customer_id,
            "portfolio_id": portfolio_id,
            "holdings": holdings,
        }
        final_state = await graph.ainvoke(initial_state)

        logger.info(
            "advisor_run_completed",
            portfolio_id=portfolio_id,
            error=final_state.get("error"),
            orders=len(final_state.get("orders", [])),
        )
        return AdvisorResult(
            customer_id=customer_id,
            portfolio_id=portfolio_id,
            weights=dict(final_state.get("weights", {})),
            orders=list(final_state.get("orders", [])),
            report=str(final_state.get("report", "")),
            error=final_state.get("error"),
        )


__all__ = ["AdvisorService", "AdvisorResult"]
