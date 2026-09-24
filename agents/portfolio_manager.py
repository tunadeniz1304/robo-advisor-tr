"""Portföy Yöneticisi — optimise, propose, await approval, execute, report.

LangGraph nodes of the rebalancing workflow:

    * :class:`OptimizerAgent` — target allocation for the effective risk
      level (suitability gate enforced).
    * :class:`ProposalAgent` — persists a proposal (``ONAY_BEKLIYOR``) with
      minimum-turnover orders, costs, taxes, explanation cards and the
      grounded LLM rationale. The LLM cannot trigger an execution.
    * :func:`approval_node` — pauses the graph with ``interrupt`` until a
      human approves or rejects (resume via ``Command(resume=…)``).
    * :class:`ExecutionAgent` — fills the approved proposal exactly once
      through the simulated broker, or records the rejection.
    * :func:`report_node` — deterministic execution summary.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from agents.state import RebalanceState
from core.logging import get_logger
from llm.fmt import tl
from llm.prompts import DISCLAIMER
from services.optimization.service import OptimizationRequest, OptimizationService
from services.optimization.strategies import OptimizationError
from services.rebalancing.proposals import ProposalError, ProposalService

logger = get_logger("otonom.agent.portfolio")


class OptimizerAgent:
    """Runs the optimiser for the effective level."""

    def __init__(self, optimizer: OptimizationService, proposals: ProposalService) -> None:
        self._opt = optimizer
        self._proposals = proposals

    def node(self) -> Callable[[RebalanceState], Awaitable[dict[str, Any]]]:
        async def run(state: RebalanceState) -> dict[str, Any]:
            level = state.get("level") or {}
            regime = state.get("regime") or {}
            try:
                result = await self._opt.optimize(
                    OptimizationRequest(
                        level=int(level.get("level", 5)),
                        method=state.get("method"),
                        override_ack=bool(state.get("override_ack")),
                        views=await self._proposals.approved_views(),
                        regime_tilt=float(regime.get("tilt", 0.0)),
                    )
                )
            except OptimizationError as exc:
                return {"error": str(exc)}
            if not result.gate.allowed:
                return {"error": "Önerilen dağılım risk seviyeniz için uygun değildir."}
            return {"optimization": result.to_dict(), "error": None}

        return run


class ProposalAgent:
    """Persists the proposal awaiting human approval."""

    def __init__(self, proposals: ProposalService) -> None:
        self._proposals = proposals

    def node(self) -> Callable[..., Awaitable[dict[str, Any]]]:
        async def run(state: RebalanceState, config: RunnableConfig) -> dict[str, Any]:
            thread_id = (config.get("configurable") or {}).get("thread_id")
            try:
                outcome = await self._proposals.create(
                    int(state["portfolio_id"]),
                    actor=str(state.get("actor") or "system"),
                    actor_role=state.get("actor_role"),
                    actor_user_id=state.get("user_id"),
                    source=str(state.get("source") or "manual"),
                    trigger=str(state.get("trigger") or "manual"),
                    idempotency_key=state.get("idempotency_key"),
                    override_ack=bool(state.get("override_ack")),
                    regime=state.get("regime"),
                    graph_thread_id=str(thread_id) if thread_id else None,
                    optimization=state.get("optimization"),
                )
            except ProposalError as exc:
                return {"error": str(exc)}
            if outcome.proposal is None:
                return {
                    "needs_rebalance": False,
                    "message": outcome.message,
                    "proposal_id": None,
                    "proposal_status": None,
                    "error": None,
                }
            p = outcome.proposal
            return {
                "needs_rebalance": True,
                "proposal_id": p.id,
                "proposal_status": p.status,
                "llm_mode": p.llm_mode,
                "report": p.rationale,
                "error": None,
            }

        return run


async def approval_node(state: RebalanceState) -> dict[str, Any]:
    """Human-in-the-loop: pause until the proposal is approved or rejected."""
    decision = interrupt(
        {
            "proposal_id": state.get("proposal_id"),
            "message": "Öneri onayınızı bekliyor. Onaylanmadan hiçbir emir yürütülmez.",
        }
    )
    return {"decision": decision if isinstance(decision, dict) else {"approved": bool(decision)}}


class ExecutionAgent:
    """Executes (or records the rejection of) the approved proposal."""

    def __init__(self, proposals: ProposalService) -> None:
        self._proposals = proposals

    def node(self) -> Callable[[RebalanceState], Awaitable[dict[str, Any]]]:
        async def run(state: RebalanceState) -> dict[str, Any]:
            pid = state.get("proposal_id")
            decision = state.get("decision") or {}
            if not pid:
                return {"error": "Öneri kimliği yok."}
            try:
                if decision.get("approved"):
                    proposal = await self._proposals.execute(
                        int(pid), actor=str(decision.get("actor") or "system")
                    )
                else:
                    proposal = await self._proposals.get(int(pid))
                    if proposal.status in ("ONAY_BEKLIYOR", "TASLAK"):
                        proposal = await self._proposals.reject(
                            int(pid),
                            user_id=decision.get("user_id"),
                            role=decision.get("role"),
                            reason=str(decision.get("reason") or "Kullanıcı reddetti."),
                        )
            except ProposalError as exc:
                return {"error": str(exc)}
            return {
                "proposal_status": proposal.status,
                "execution": proposal.execution_report or {},
                "error": None,
            }

        return run


async def report_node(state: RebalanceState) -> dict[str, Any]:
    """Deterministic run summary."""
    status = state.get("proposal_status")
    pid = state.get("proposal_id")
    if status == "YURUTULDU":
        rep = state.get("execution") or {}
        summary = (
            f"Öneri #{pid} yürütüldü: {len(rep.get('fills') or [])} işlem, toplam maliyet "
            f"{tl(float(rep.get('total_fees', 0)), 2)}, stopaj {tl(float(rep.get('total_tax', 0)), 2)}."
        )
    elif status == "REDDEDILDI":
        summary = f"Öneri #{pid} reddedildi; portföy değişmedi."
    else:
        summary = f"Öneri #{pid} durumu: {status}."
    return {"report": f"{summary}\n{DISCLAIMER}"}


__all__ = ["ExecutionAgent", "OptimizerAgent", "ProposalAgent", "approval_node", "report_node"]
