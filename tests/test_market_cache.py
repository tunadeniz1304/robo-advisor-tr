"""Tests for the MarketService TTL cache.

Uses a counting source so cache hits vs network downloads are observable:
two fetches of the same symbol set within the TTL must produce a single
download_history call, and invalidate_cache() must force a reload.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services.market_service import MarketDataSource, MarketService


class _CountingSource(MarketDataSource):
    """Minimal source that records how many times it is called."""

    def __init__(self) -> None:
        self.calls = 0

    async def download_history(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        self.calls += 1
        return {
            sym: pd.DataFrame(
                {"Close": np.linspace(100.0, 120.0, 40)},
                index=pd.bdate_range(end=pd.Timestamp.today(), periods=40),
            )
            for sym in symbols
        }


@pytest.mark.asyncio
async def test_market_cache_hit_within_ttl_single_download() -> None:
    """Repeated fetches of the same symbol within TTL hit the cache."""
    source = _CountingSource()
    svc = MarketService(source=source, cache_ttl_seconds=300)

    first = await svc.fetch_snapshots(["AAA"])
    second = await svc.fetch_snapshots(["AAA"])

    assert source.calls == 1, "cache should serve the second call"
    assert set(first) == set(second) == {"AAA"}
    assert first["AAA"].last_price == pytest.approx(120.0)


@pytest.mark.asyncio
async def test_market_cache_invalidate_forces_reload() -> None:
    """invalidate_cache drops the entry so the next fetch re-downloads."""
    source = _CountingSource()
    svc = MarketService(source=source, cache_ttl_seconds=300)

    await svc.fetch_snapshots(["AAA"])
    svc.invalidate_cache(["AAA"])
    await svc.fetch_snapshots(["AAA"])

    assert source.calls == 2, "invalidate must force a fresh download"


@pytest.mark.asyncio
async def test_market_cache_different_symbols_miss() -> None:
    """Different symbol sets are separate cache keys (each downloads)."""
    source = _CountingSource()
    svc = MarketService(source=source, cache_ttl_seconds=300)

    await svc.fetch_snapshots(["AAA"])
    await svc.fetch_snapshots(["BBB"])

    assert source.calls == 2
