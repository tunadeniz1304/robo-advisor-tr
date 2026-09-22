"""LangGraph workflow assembly for the Robo-Advisor.

Builds the stateful graph:

    START -> market_agent -> risk_agent -> portfolio_manager -> END

with conditional edges: when any node writes ``state["error"]`` the workflow
short-circuits to ``END`` instead of feeding invalid data downstream.

State persistence:
    * A durable SQLite checkpointer (``AsyncSqliteSaver``) keyed by
      ``thread_id`` is used when ``checkpoint_db`` is provided — mid-run state
      is checkpointed to disk, enabling replay and audits.
    * Otherwise an in-memory checkpointer is used (tests / dev).

The workflow is assembled *per request* through dependency injection (market
service, risk service, MPT service, LLM client), so it is fully decoupled from
global state and trivially testable with deterministic doubles.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from langgraph.graph import END, START, StateGraph

from agents.market_agent import MarketAgent
from agents.portfolio_manager import PortfolioManagerAgent
from agents.risk_agent import RiskAgent
from agents.state import AdvisorState
from core.logging import get_logger
from llm.clients import LLMClient
from services.market_service import MarketService
from services.portfolio_service import PortfolioService
from services.risk_service import RiskService

logger = get_logger("otonom.graph")


class AdvisorGraph:
    """Ready-to-invoke LangGraph application with an attached checkpointer.

    Attributes:
        compiled: The compiled :class:`StateGraph` application.
        thread_id: Stable identifier under which state is checkpointed.
        checkpointer: The (async) checkpointer bound to the graph; kept alive
            by this object so subsequent ``invoke`` calls can resume state.
    """

    def __init__(self, compiled, thread_id: str, checkpointer=None) -> None:
        self.compiled = compiled
        self.thread_id = thread_id
        self.checkpointer = checkpointer

    async def ainvoke(self, initial_state: dict) -> dict:
        """Invoke and return the final state as a plain dict."""
        config = {"configurable": {"thread_id": self.thread_id}}
        result = await self.compiled.ainvoke(initial_state, config=config)
        return dict(result)


def build_advisor_graph(
    *,
    market_service: MarketService,
    risk_service: RiskService,
    portfolio_service: PortfolioService,
    llm_client: LLMClient,
    thread_id: str,
    checkpoint_db: Optional[str | Path] = None,
) -> AdvisorGraph:
    """Compile the advisor workflow into a runnable graph.

    Args:
        market_service: Real/fake market data source for the Market Agent.
        risk_service: Dynamic risk scorer.
        portfolio_service: Markowitz MPT engine.
        llm_client: Real LLM client (OpenAI/Anthropic) for narratives.
        thread_id: Stable checkpoint namespace (e.g. ``portfolio-3``).
        checkpoint_db: Optional path to a SQLite file for durable checkpoints.
            When ``None`` an in-memory checkpointer is used.

    Returns:
        An :class:`AdvisorGraph` owning the compiled application.
    """
    graph = StateGraph(AdvisorState)

    graph.add_node("market_agent", MarketAgent(market_service).node())
    graph.add_node("risk_agent", RiskAgent(risk_service).node())
    graph.add_node(
        "portfolio_manager",
        PortfolioManagerAgent(portfolio_service, llm_client).node(),
    )

    graph.add_edge(START, "market_agent")

    # Conditional edges: error -> END, success -> next node.
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
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        saver = AsyncSqliteSaver.from_conn_string(str(checkpoint_db))
        compiled = graph.compile(checkpointer=saver)
        return AdvisorGraph(compiled=compiled, thread_id=thread_id, checkpointer=saver)

    compiled = graph.compile()
    logger.info("advisor_graph_compiled", thread_id=thread_id, durable=checkpoint_db is not None)
    return AdvisorGraph(compiled=compiled, thread_id=thread_id)


__all__ = ["AdvisorGraph", "build_advisor_graph"]
