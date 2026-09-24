"""Piyasa Ajanı (Market Agent) — LangGraph node.

Warms the shared market cache for the portfolio's holdings plus the
investment universe and records which data source served the run (live
Yahoo Finance or the offline snapshot). Only a small summary is written to
the graph state; price panels stay in the service cache so checkpoints stay
small.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from agents.state import RebalanceState
from core.database import session_factory
from core.logging import get_logger
from models import Portfolio
from services.market_data.universe import UNIVERSE
from services.market_service import MarketService

logger = get_logger("otonom.agent.market")

# Geriye dönük uyumluluk: varsayılan BIST sepeti.
DEFAULT_UNIVERSE: tuple[str, ...] = ("THYAO.IS", "AKBNK.IS", "ASELS.IS", "SAHOL.IS")


class MarketAgent:
    """Node factory binding the shared market service."""

    def __init__(self, market_service: MarketService) -> None:
        self._market = market_service

    def node(self) -> Callable[[RebalanceState], Awaitable[dict[str, Any]]]:
        async def run(state: RebalanceState) -> dict[str, Any]:
            portfolio_id = state.get("portfolio_id")
            holdings: dict[str, Any] = {}
            if portfolio_id:
                async with session_factory() as session:
                    portfolio = await session.get(Portfolio, int(portfolio_id))
                    if portfolio is None:
                        return {"error": f"Portfolio {portfolio_id} bulunamadı."}
                    holdings = dict(portfolio.holdings or {})
            symbols = sorted(
                {s.symbol for s in UNIVERSE} | {k for k, v in holdings.items() if float(v) > 0}
            )
            try:
                snapshots = await self._market.fetch_snapshots(symbols)
            except RuntimeError as exc:
                logger.error("market_agent_failed", error=str(exc))
                return {"error": "Piyasa verisi alınamadı."}
            if not snapshots:
                return {"error": "Piyasa verisi indirilemedi: hiçbir sembol için veri yok."}
            status_fn = getattr(self._market, "data_status", None)
            status = status_fn() if callable(status_fn) else {"source": "custom"}
            logger.info(
                "market_agent_completed", symbols=len(snapshots), source=status.get("source")
            )
            return {
                "market": {
                    "symbols": len(snapshots),
                    "data_source": status.get("source"),
                    "snapshot_end": status.get("snapshot_end"),
                },
                "error": None,
            }

        return run


__all__ = ["DEFAULT_UNIVERSE", "MarketAgent"]
