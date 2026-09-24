"""LangGraph workflow of the rebalancing process (human in the loop).

    START → market → suitability → optimize → propose ─┬─► approval (interrupt)
                                                        │        │
                                                        │        ▼
                                                        │     execute → report → END
                                                        └─► END (no rebalance needed / error)

* Any node writing ``state["error"]`` short-circuits to ``END``.
* ``approval`` calls :func:`langgraph.types.interrupt`; the run is persisted
  by the checkpointer and resumed with ``Command(resume={...})`` when a
  human approves or rejects the proposal.
* The graph is compiled once per :class:`AdvisorGraph`; production uses the
  durable ``AsyncSqliteSaver`` (``CHECKPOINT_DB``), tests an in-memory saver.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from agents.market_agent import MarketAgent
from agents.portfolio_manager import (
    ExecutionAgent,
    OptimizerAgent,
    ProposalAgent,
    approval_node,
    report_node,
)
from agents.risk_agent import RiskAgent
from agents.state import RebalanceState
from core.logging import get_logger
from services.market_service import MarketService
from services.optimization.service import OptimizationService
from services.rebalancing.proposals import ProposalService

logger = get_logger("otonom.graph")


class AdvisorGraph:
    """Compiled graph plus its checkpointer (and owned connection)."""

    def __init__(self, compiled: Any, checkpointer: Any, _conn: Any = None) -> None:
        self.compiled = compiled
        self.checkpointer = checkpointer
        self._conn = _conn

    async def start(self, initial_state: dict[str, Any], thread_id: str) -> dict[str, Any]:
        """Run until the approval interrupt (or the end)."""
        config = {"configurable": {"thread_id": thread_id}}
        result = await self.compiled.ainvoke(initial_state, config=config)
        return dict(result)

    async def resume(self, thread_id: str, decision: dict[str, Any]) -> dict[str, Any]:
        """Resume a paused run with the human decision."""
        config = {"configurable": {"thread_id": thread_id}}
        result = await self.compiled.ainvoke(Command(resume=decision), config=config)
        return dict(result)

    async def is_waiting(self, thread_id: str) -> bool:
        """Whether the thread is paused at the approval interrupt."""
        snapshot = await self.compiled.aget_state({"configurable": {"thread_id": thread_id}})
        return bool(snapshot and snapshot.next and "approval" in snapshot.next)

    async def aclose(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None


def _route(next_node: str) -> Any:
    return lambda s: END if s.get("error") else next_node


async def build_rebalance_graph(
    *,
    market_service: MarketService,
    optimizer: OptimizationService,
    proposals: ProposalService,
    checkpoint_db: str | Path | None = None,
) -> AdvisorGraph:
    """Compile the workflow with a durable (SQLite) or in-memory checkpointer."""
    graph = StateGraph(RebalanceState)
    graph.add_node("market", MarketAgent(market_service).node())  # type: ignore[call-overload,arg-type]
    graph.add_node("suitability", RiskAgent().node())  # type: ignore[call-overload,arg-type]
    graph.add_node("optimize", OptimizerAgent(optimizer, proposals).node())  # type: ignore[call-overload,arg-type]
    graph.add_node("propose", ProposalAgent(proposals).node())  # type: ignore[call-overload,arg-type]
    graph.add_node("approval", approval_node)  # type: ignore[call-overload,arg-type]
    graph.add_node("execute", ExecutionAgent(proposals).node())  # type: ignore[call-overload,arg-type]
    graph.add_node("report", report_node)  # type: ignore[call-overload,arg-type]

    graph.add_edge(START, "market")
    graph.add_conditional_edges("market", _route("suitability"), ["suitability", END])
    graph.add_conditional_edges("suitability", _route("optimize"), ["optimize", END])
    graph.add_conditional_edges("optimize", _route("propose"), ["propose", END])
    graph.add_conditional_edges(
        "propose",
        lambda s: END if s.get("error") or not s.get("proposal_id") else "approval",
        ["approval", END],
    )
    graph.add_edge("approval", "execute")
    graph.add_edge("execute", "report")
    graph.add_edge("report", END)

    if checkpoint_db is not None:
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        conn = await aiosqlite.connect(str(checkpoint_db))
        saver = AsyncSqliteSaver(conn)
        logger.info("rebalance_graph_compiled", durable=True)
        return AdvisorGraph(graph.compile(checkpointer=saver), saver, _conn=conn)
    saver_mem = InMemorySaver()
    logger.info("rebalance_graph_compiled", durable=False)
    return AdvisorGraph(graph.compile(checkpointer=saver_mem), saver_mem)


__all__ = ["AdvisorGraph", "build_rebalance_graph"]
