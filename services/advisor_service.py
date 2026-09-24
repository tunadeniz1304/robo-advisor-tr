"""Advisor service — orchestrates a Robo-Advisor run end-to-end.

Responsibilities:
    1. Validate that the portfolio exists and belongs to the customer.
    2. Run the LangGraph workflow (compiled once, reused for every run; each
       run gets a unique checkpoint thread).
    3. Return a structured :class:`AdvisorResult`.

LLM availability never blocks the workflow: the gateway falls back to the
deterministic demo output, so there is no 503 path anymore (bug #8).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from agents.graph import AdvisorGraph, build_advisor_graph
from core.config import Settings
from core.database import session_factory
from core.logging import get_logger
from llm.clients import LLMClient
from llm.gateway import LLMGateway
from models import Portfolio
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
    orders: list[dict[str, Any]] = field(default_factory=list)
    report: str = ""
    error: str | None = None
    llm_mode: str | None = None
    llm_error_kind: str | None = None
    run_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "customer_id": self.customer_id,
            "portfolio_id": self.portfolio_id,
            "weights": self.weights,
            "orders": self.orders,
            "report": self.report,
            "error": self.error,
            "llm_mode": self.llm_mode,
            "llm_error_kind": self.llm_error_kind,
            "run_id": self.run_id,
        }


class AdvisorService:
    """Runs the multi-agent rebalancing workflow for one portfolio."""

    def __init__(
        self,
        settings: Settings,
        market_service: MarketService | None = None,
        risk_service: RiskService | None = None,
        portfolio_service: PortfolioService | None = None,
        llm_client: LLMClient | None = None,
        checkpoint_db: str | None = None,
        gateway: LLMGateway | None = None,
        risk_free_rate: float = 0.0,
    ) -> None:
        self._settings = settings
        if market_service is None:
            from services.market_service import YFinanceSource

            market_service = MarketService(YFinanceSource())
        self._market = market_service
        self._risk = risk_service or RiskService()
        self._mpt = portfolio_service or PortfolioService()
        self._gateway = gateway or LLMGateway(settings, client=llm_client)
        self._checkpoint_db = checkpoint_db
        self._rf = risk_free_rate
        self._graph: AdvisorGraph | None = None
        self._lock = asyncio.Lock()

    @property
    def gateway(self) -> LLMGateway:
        return self._gateway

    async def graph(self) -> AdvisorGraph:
        """Compile the graph once (thread-safe) and return it."""
        if self._graph is None:
            async with self._lock:
                if self._graph is None:
                    self._graph = await build_advisor_graph(
                        market_service=self._market,
                        risk_service=self._risk,
                        portfolio_service=self._mpt,
                        gateway=self._gateway,
                        checkpoint_db=self._checkpoint_db,
                        risk_free_rate=self._rf,
                    )
        return self._graph

    async def aclose(self) -> None:
        """Close the durable checkpointer connection."""
        if self._graph is not None:
            await self._graph.aclose()
            self._graph = None

    async def run_rebalance(self, portfolio_id: int, customer_id: int) -> AdvisorResult:
        """Execute the LangGraph workflow for a portfolio.

        Args:
            portfolio_id: Portfolio to rebalance (must exist in DB).
            customer_id: Owner of the portfolio (must match DB).

        Returns:
            :class:`AdvisorResult` with weights/orders/report or an error.
        """
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

        run_id = f"rebalance-{portfolio_id}-{uuid.uuid4().hex[:12]}"
        graph = await self.graph()
        final_state = await graph.ainvoke(
            {"customer_id": customer_id, "portfolio_id": portfolio_id, "holdings": holdings},
            thread_id=run_id,
        )
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
            llm_mode=final_state.get("llm_mode"),
            llm_error_kind=final_state.get("llm_error_kind"),
            run_id=run_id,
        )


__all__ = ["AdvisorResult", "AdvisorService"]
