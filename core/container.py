"""Application service container (dependency injection root).

One :class:`Container` per FastAPI app holds the long-lived, shared services:
the market data service (with its TTL cache — bug #7: routers used to build a
new service per request, defeating the cache), the LLM gateway and the
advisor service (whose LangGraph is compiled once — bug #14).

Tests build the container with injected doubles (``market_source``,
``llm_client``) instead of monkeypatching globals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.config import Settings
from core.logging import get_logger
from llm.clients import LLMClient, get_llm_client
from llm.gateway import LLMGateway
from services.advisor_service import AdvisorService
from services.market_data.service import MarketDataService
from services.market_service import MarketDataSource

logger = get_logger("otonom.container")


async def record_llm_usage(row: dict[str, Any]) -> None:
    """Persist one LLM usage row (best effort, separate session)."""
    from core.database import session_factory
    from models import LLMUsage

    async with session_factory() as session:
        session.add(
            LLMUsage(
                purpose=str(row.get("purpose", ""))[:48],
                mode=str(row.get("mode", ""))[:16],
                model=str(row.get("model", ""))[:80],
                prompt_tokens=int(row.get("prompt_tokens", 0)),
                completion_tokens=int(row.get("completion_tokens", 0)),
                latency_ms=float(row.get("latency_ms", 0.0)),
                error_kind=row.get("error_kind"),
            )
        )
        await session.commit()


@dataclass
class Container:
    """Shared services of one application instance."""

    settings: Settings
    market: MarketDataService
    gateway: LLMGateway
    advisor: AdvisorService
    extras: dict[str, Any] = field(default_factory=dict)

    async def aclose(self) -> None:
        await self.advisor.aclose()


def default_market_source(settings: Settings) -> MarketDataSource:
    """Live Yahoo Finance → offline snapshot chain (``DATA_MODE``)."""
    from services.market_data.sources import ChainedSource, LiveYahooSource, SnapshotSource

    snapshot = SnapshotSource()
    live = None if settings.data_mode == "snapshot" else LiveYahooSource(macro_provider=snapshot)
    return ChainedSource(live, snapshot, mode=settings.data_mode)


def build_container(
    settings: Settings,
    *,
    market_source: MarketDataSource | None = None,
    llm_client: LLMClient | None = None,
) -> Container:
    """Wire the shared services for an app.

    Args:
        settings: Validated settings.
        market_source: Optional market source override (tests: offline).
        llm_client: Optional LLM client override (tests: fakes).

    Returns:
        The :class:`Container`.
    """
    market = MarketDataService(
        market_source or default_market_source(settings),
        cache_ttl_seconds=settings.market_cache_ttl_seconds,
    )
    client = llm_client if llm_client is not None else get_llm_client(settings)
    gateway = LLMGateway(settings, client=client, recorder=record_llm_usage)
    advisor = AdvisorService(
        settings=settings,
        market_service=market,
        gateway=gateway,
        checkpoint_db=settings.checkpoint_db,
        risk_free_rate=market.risk_free_rate(),
    )
    return Container(settings=settings, market=market, gateway=gateway, advisor=advisor)


__all__ = ["Container", "build_container", "record_llm_usage"]
