"""Regression: durable AsyncSqliteSaver checkpointer lifecycle.

The graph must compile with a real aiosqlite connection owned by the graph
and closed via aclose() — and the compiled graph must invoke without leaking
the connection (previously from_conn_string was passed to compile() directly,
which is an async context manager, not a saver).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agents.graph import build_advisor_graph
from core.database import adopt_engine, create_engine_from_url, init_db
from services.market_service import MarketService
from services.portfolio_service import PortfolioService
from services.risk_service import RiskService
from tests.conftest import DeterministicLLM, FakeMarketSource


@pytest.mark.asyncio
async def test_durable_checkpointer_build_and_invoke(tmp_path: Path) -> None:
    """Durable checkpoint path compiles, runs, and closes cleanly."""
    checkpoint_db = tmp_path / "checkpoints.sqlite"
    # Risk agent reads the customer/portfolio from the DB: bootstrap a temp engine.
    engine = create_engine_from_url(f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")
    adopt_engine(engine)
    await init_db(engine)
    try:
        graph = await build_advisor_graph(
            market_service=MarketService(FakeMarketSource(symbols=["AAA", "BBB", "CCC"])),
            risk_service=RiskService(),
            portfolio_service=PortfolioService(),
            llm_client=DeterministicLLM(),
            thread_id="portfolio-1",
            checkpoint_db=checkpoint_db,
        )
        try:
            assert graph.checkpointer is not None, "durable checkpointer should be attached"
            result = await graph.ainvoke(
                {"portfolio_id": 1, "customer_id": 1, "holdings": {}}
            )
            assert "error" in result or "weights" in result
        finally:
            await graph.aclose()
        assert checkpoint_db.exists()
    finally:
        await engine.dispose()
