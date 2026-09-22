"""Piyasa Ajanı (Market Agent) — LangGraph node.

Fetches *real* price history for every ticker referenced by the portfolio
(plus a configurable default universe when holdings are empty) through the
asynchronous :class:`services.market_service.MarketService`, and writes a
JSON-serialisable ``market`` snapshot into the graph state for downstream
nodes. Failure handling: a download error becomes a first-class state error —
the graph short-circuits with a clear message instead of propagating raw
network exceptions into the API layer.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from agents.state import AdvisorState
from core.logging import get_logger
from services.market_service import MarketService

logger = get_logger("otonom.agent.market")

# Varsayılan varlık evreni: portföy hiç varlık tutmuyorken Markowitz için
# minimum iki varlıklı bir başlangıç seti. Borsa İstanbul hisseleri.
DEFAULT_UNIVERSE: tuple[str, ...] = (
    "THYAO.IS",
    "AKBNK.IS",
    "ASELS.IS",
    "SAHOL.IS",
)


class MarketAgent:
    """Node factory; binds a :class:`MarketService` and returns a node fn."""

    def __init__(self, market_service: MarketService) -> None:
        self._market = market_service

    def node(self) -> Callable[[AdvisorState], Awaitable[dict]]:
        """Return the async node function runnable by LangGraph."""

        async def run(state: AdvisorState) -> dict:
            # Ticker set: portföydeki varlıklar ya da varsayılan evren.
            holdings = state.get("holdings") or {}
            tickers = [t for t in holdings if isinstance(t, str)] or list(DEFAULT_UNIVERSE)

            try:
                snapshots = await self._market.fetch_snapshots(tickers)
            except RuntimeError as exc:
                logger.error("market_agent_failed", error=str(exc))
                return {"error": str(exc)}

            if not snapshots:
                return {
                    "error": "Piyasa verisi indirilemedi: hiçbir sembol için yeterli veri yok.",
                    "market": {},
                }

            market = {
                ticker: snap.to_dict() for ticker, snap in snapshots.items()
            }
            # aligned returns frame'ini yalnızca snapshot'lardan çıkarıp
            # state içinde kullanılabilir (JSON) forma indir.
            returns = self._market.build_returns_frame(snapshots)
            returns_payload: dict[str, object] = {}
            if not returns.empty:
                returns_payload = {
                    idx.date().isoformat(): {
                        col: float(row[col]) for col in returns.columns
                    }
                    for idx, row in returns.iterrows()
                }

            logger.info(
                "market_agent_completed",
                tickers=list(market.keys()),
                observations=len(returns_payload),
            )
            return {
                "market": market,
                "_returns": returns_payload,
                "error": None,
            }

        return run
