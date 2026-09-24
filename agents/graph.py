"""LangGraph workflow assembly for the Robo-Advisor.

Builds the stateful graph:

    START -> market_agent -> risk_agent -> portfolio_manager -> END

with conditional edges: when any node writes ``state["error"]`` the workflow
short-circuits to ``END`` instead of feeding invalid data downstream.

Lifecycle (bug #14):
    * the graph is compiled **once** per :class:`AdvisorGraph` (the app
      compiles it at startup) and reused for every run;
    * a durable SQLite checkpointer (``AsyncSqliteSaver``) is attached when
      ``checkpoint_db`` is given — production always passes it;
    * each run uses its own ``thread_id`` so checkpoints never mix runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

from agents.market_agent import MarketAgent
from agents.portfolio_manager import PortfolioManagerAgent
from agents.risk_agent import RiskAgent
from agents.state import AdvisorState
from core.config import Settings
from core.logging import get_logger
from llm.clients import LLMClient
from llm.gateway import LLMGateway
from services.market_service import MarketService
from services.portfolio_service import PortfolioService
from services.risk_service import RiskService

logger = get_logger("otonom.graph")


class AdvisorGraph:
    """Compiled LangGraph application with its (optional) durable checkpointer.

    Attributes:
        compiled: The compiled :class:`StateGraph` application.
        thread_id: Default checkpoint namespace (per-run ids override it).
        checkpointer: The async checkpointer bound to the graph, if any.
    """

    def __init__(
        self, compiled: Any, thread_id: str, checkpointer: Any = None, _conn: Any = None
    ) -> None:
        self.compiled = compiled
        self.thread_id = thread_id
        self.checkpointer = checkpointer
        self._conn = _conn

    async def ainvoke(
        self, initial_state: dict[str, Any], thread_id: str | None = None
    ) -> dict[str, Any]:
        """Invoke and return the final state as a plain dict."""
        config = {"configurable": {"thread_id": thread_id or self.thread_id}}
        result = await self.compiled.ainvoke(initial_state, config=config)
        return dict(result)

    async def aclose(self) -> None:
        """Release the durable checkpoint connection, if any."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None


def _as_gateway(llm_client: LLMClient | None, gateway: LLMGateway | None) -> LLMGateway:
    if gateway is not None:
        return gateway
    if llm_client is None:
        raise ValueError("llm_client veya gateway verilmelidir.")
    return LLMGateway(Settings(), client=llm_client)


async def build_advisor_graph(
    *,
    market_service: MarketService,
    risk_service: RiskService,
    portfolio_service: PortfolioService,
    llm_client: LLMClient | None = None,
    gateway: LLMGateway | None = None,
    thread_id: str = "advisor",
    checkpoint_db: str | Path | None = None,
    risk_free_rate: float = 0.0,
) -> AdvisorGraph:
    """Compile the advisor workflow into a runnable graph.

    Args:
        market_service: Shared market data service (cached).
        risk_service: Dynamic risk scorer.
        portfolio_service: Markowitz MPT engine.
        llm_client: LLM client (wrapped into a gateway when no gateway given).
        gateway: Pre-built LLM gateway (preferred).
        thread_id: Default checkpoint namespace.
        checkpoint_db: SQLite file for durable checkpoints; ``None`` → none.
        risk_free_rate: Annual TL risk-free rate for the optimiser.

    Returns:
        An :class:`AdvisorGraph` owning the compiled application.
    """
    graph = StateGraph(AdvisorState)

    graph.add_node("market_agent", MarketAgent(market_service).node())  # type: ignore[call-overload]
    graph.add_node("risk_agent", RiskAgent(risk_service).node())  # type: ignore[call-overload]
    graph.add_node(  # type: ignore[call-overload]
        "portfolio_manager",
        PortfolioManagerAgent(
            portfolio_service, _as_gateway(llm_client, gateway), risk_free_rate=risk_free_rate
        ).node(),
    )

    graph.add_edge(START, "market_agent")
    graph.add_conditional_edges(
        "market_agent",
        lambda s: "risk_agent" if not s.get("error") else END,
        {"risk_agent": "risk_agent", END: END},
    )
    graph.add_conditional_edges(
        "risk_agent",
        lambda s: "portfolio_manager" if not s.get("error") else END,
        {"portfolio_manager": "portfolio_manager", END: END},
    )
    graph.add_edge("portfolio_manager", END)

    if checkpoint_db is not None:
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        conn = await aiosqlite.connect(str(checkpoint_db))
        saver = AsyncSqliteSaver(conn)
        compiled = graph.compile(checkpointer=saver)
        logger.info("advisor_graph_compiled", durable=True)
        return AdvisorGraph(compiled=compiled, thread_id=thread_id, checkpointer=saver, _conn=conn)

    compiled = graph.compile()
    logger.info("advisor_graph_compiled", durable=False)
    return AdvisorGraph(compiled=compiled, thread_id=thread_id)


__all__ = ["AdvisorGraph", "build_advisor_graph"]
