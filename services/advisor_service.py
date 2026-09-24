"""Advisor service — drives the human-in-the-loop rebalancing graph.

* :meth:`AdvisorService.start` runs market → suitability → optimise →
  propose and pauses at the approval interrupt; the result carries the
  persisted proposal (``ONAY_BEKLIYOR``).
* :meth:`AdvisorService.approve` / :meth:`reject` apply the decision:
  the proposal service performs the atomic state transition, then the paused
  graph thread resumes and executes (exactly once) and reports. Proposals
  created outside the graph (scheduler, copilot) are executed directly.

The graph is compiled once (lazily, thread-safe) with the durable
checkpointer. LLM availability never blocks the workflow.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from agents.graph import AdvisorGraph, build_rebalance_graph
from core.config import Settings
from core.database import session_factory
from core.logging import get_logger
from llm.clients import LLMClient
from llm.gateway import LLMGateway
from llm.prompts import DISCLAIMER
from models import Portfolio
from models.execution import PROPOSAL_APPROVED
from services.market_data.service import MarketDataService
from services.market_service import MarketService
from services.optimization.service import OptimizationService
from services.rebalancing.proposals import ProposalService, proposal_to_dict

logger = get_logger("otonom.advisor")


@dataclass
class AdvisorResult:
    """Outcome of starting a rebalancing run."""

    customer_id: int
    portfolio_id: int
    weights: dict[str, float] = field(default_factory=dict)
    orders: list[dict[str, Any]] = field(default_factory=list)
    report: str = ""
    error: str | None = None
    llm_mode: str | None = None
    run_id: str | None = None
    proposal_id: int | None = None
    status: str | None = None
    needs_rebalance: bool = True
    message: str = ""
    proposal: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "customer_id": self.customer_id,
            "portfolio_id": self.portfolio_id,
            "weights": self.weights,
            "orders": self.orders,
            "report": self.report,
            "error": self.error,
            "llm_mode": self.llm_mode,
            "run_id": self.run_id,
            "proposal_id": self.proposal_id,
            "status": self.status,
            "needs_rebalance": self.needs_rebalance,
            "message": self.message,
            "proposal": self.proposal,
        }


class AdvisorService:
    """Runs and resumes the rebalancing workflow."""

    def __init__(
        self,
        settings: Settings,
        market_service: MarketService | None = None,
        llm_client: LLMClient | None = None,
        *,
        gateway: LLMGateway | None = None,
        optimizer: OptimizationService | None = None,
        proposals: ProposalService | None = None,
        checkpoint_db: str | None = None,
        **_legacy: Any,
    ) -> None:
        self._settings = settings
        if market_service is None or not isinstance(market_service, MarketDataService):
            from services.market_data.sources import ChainedSource, SnapshotSource

            source = (
                market_service._source
                if market_service is not None
                else ChainedSource(None, SnapshotSource(), "snapshot")
            )  # noqa: SLF001
            market_service = MarketDataService(source)
        self._market = market_service
        self._gateway = gateway or LLMGateway(settings, client=llm_client)
        self._optimizer = optimizer or OptimizationService(market_service)
        self.proposals = proposals or ProposalService(
            market_service, self._optimizer, self._gateway
        )
        self._checkpoint_db = checkpoint_db
        self._graph: AdvisorGraph | None = None
        self._lock: asyncio.Lock | None = None

    @property
    def gateway(self) -> LLMGateway:
        return self._gateway

    async def graph(self) -> AdvisorGraph:
        """Compile the graph once and return it."""
        if self._graph is None:
            if self._lock is None:
                self._lock = asyncio.Lock()
            async with self._lock:
                if self._graph is None:
                    self._graph = await build_rebalance_graph(
                        market_service=self._market,
                        optimizer=self._optimizer,
                        proposals=self.proposals,
                        checkpoint_db=self._checkpoint_db,
                    )
        return self._graph

    async def aclose(self) -> None:
        if self._graph is not None:
            await self._graph.aclose()
            self._graph = None

    async def run_rebalance(
        self, portfolio_id: int, customer_id: int, **kwargs: Any
    ) -> AdvisorResult:
        """Backward compatible alias of :meth:`start`."""
        return await self.start(portfolio_id, customer_id, **kwargs)

    async def start(
        self,
        portfolio_id: int,
        customer_id: int,
        *,
        actor: str = "system",
        actor_role: str | None = None,
        user_id: int | None = None,
        source: str = "manual",
        trigger: str = "manual",
        method: str | None = None,
        override_ack: bool = False,
        idempotency_key: str | None = None,
        regime: dict[str, Any] | None = None,
    ) -> AdvisorResult:
        """Run the workflow up to the human-approval interrupt."""
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
        thread_id = f"rebalance-{portfolio_id}-{uuid.uuid4().hex[:12]}"
        graph = await self.graph()
        state = await graph.start(
            {
                "portfolio_id": portfolio_id,
                "customer_id": customer_id,
                "actor": actor,
                "actor_role": actor_role,
                "user_id": user_id,
                "source": source,
                "trigger": trigger,
                "method": method,
                "override_ack": override_ack,
                "idempotency_key": idempotency_key,
                "regime": regime,
            },
            thread_id,
        )
        result = AdvisorResult(
            customer_id=customer_id,
            portfolio_id=portfolio_id,
            error=state.get("error"),
            run_id=thread_id,
            llm_mode=state.get("llm_mode"),
            needs_rebalance=bool(state.get("needs_rebalance", True)),
            message=str(state.get("message") or ""),
            report=str(state.get("report") or ""),
        )
        pid = state.get("proposal_id")
        if pid:
            proposal = await self.proposals.get(int(pid))
            data = proposal_to_dict(proposal)
            result.proposal_id = proposal.id
            result.status = proposal.status
            result.weights = {k: float(v) for k, v in proposal.target_weights.items()}
            result.orders = list(proposal.orders)
            result.report = "\n".join([proposal.rationale, DISCLAIMER])
            result.llm_mode = proposal.llm_mode
            result.proposal = data
        logger.info(
            "advisor_run_started", portfolio_id=portfolio_id, proposal_id=pid, error=result.error
        )
        return result

    async def approve(
        self,
        proposal_id: int,
        *,
        user_id: int | None,
        role: str | None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Approve and execute (exactly once) a proposal."""
        proposal = await self.proposals.approve(
            proposal_id, user_id=user_id, role=role, idempotency_key=idempotency_key
        )
        if proposal.status == PROPOSAL_APPROVED:
            decision = {
                "approved": True,
                "user_id": user_id,
                "role": role,
                "actor": f"user:{user_id}",
            }
            resumed = False
            if proposal.graph_thread_id:
                graph = await self.graph()
                try:
                    if await graph.is_waiting(proposal.graph_thread_id):
                        await graph.resume(proposal.graph_thread_id, decision)
                        resumed = True
                except Exception as exc:  # noqa: BLE001 - doğrudan yürütmeye düş
                    logger.warning("graph_resume_failed", error_type=type(exc).__name__)
            proposal = await self.proposals.get(proposal_id)
            if not resumed or proposal.status == PROPOSAL_APPROVED:
                proposal = await self.proposals.execute(proposal_id, actor=f"user:{user_id}")
        return proposal_to_dict(proposal)

    async def reject(
        self, proposal_id: int, *, user_id: int | None, role: str | None, reason: str
    ) -> dict[str, Any]:
        """Reject a pending proposal and close its graph thread."""
        proposal = await self.proposals.reject(
            proposal_id, user_id=user_id, role=role, reason=reason
        )
        if proposal.graph_thread_id:
            graph = await self.graph()
            try:
                if await graph.is_waiting(proposal.graph_thread_id):
                    await graph.resume(
                        proposal.graph_thread_id,
                        {"approved": False, "user_id": user_id, "role": role, "reason": reason},
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("graph_reject_resume_failed", error_type=type(exc).__name__)
        return proposal_to_dict(await self.proposals.get(proposal_id))


__all__ = ["AdvisorResult", "AdvisorService"]
