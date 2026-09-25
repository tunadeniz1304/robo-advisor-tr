"""Market data service — real Yahoo Finance data via ``yfinance``.

The production path downloads price history asynchronously (the synchronous
``yfinance`` call is offloaded to a worker thread via :func:`asyncio.to_thread`
so the event loop is never blocked) and computes the derived quantities the
Risk and Portfolio agents need:

    * last close price per ticker,
    * 1-month momentum,
    * annualised volatility,
    * aligned daily-return series (indexed by date) for Markowitz MPT.

The service depends on a :class:`MarketDataSource` protocol rather than on
``yfinance`` directly. The production chain (:mod:`services.market_data.sources`)
talks to the network; tests inject a deterministic in-memory source that
satisfies the same protocol, so the rest of the system is verified offline
while production still uses real market data.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from typing import Protocol

import pandas as pd

from core.logging import get_logger

logger = get_logger("otonom.market")


class MarketDataSource(Protocol):
    """Protocol for a source of OHLC price histories.

    Implementations must return a mapping ``symbol -> DataFrame`` whose index
    is a ``DatetimeIndex`` and that contains at least a ``Close`` column.
    """

    async def download_history(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        """Download adjusted daily close history for the given symbols."""
        ...


@dataclass
class MarketSnapshot:
    """Derived market facts for a single ticker."""

    ticker: str
    last_price: float
    momentum_1m: float
    volatility_annualized: float
    daily_returns: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """JSON-friendly representation for the state graph checkpoint."""
        return {
            "ticker": self.ticker,
            "last_price": self.last_price,
            "momentum_1m": self.momentum_1m,
            "volatility_annualized": self.volatility_annualized,
        }


class MarketService:
    """Orchestrates downloads and derives per-ticker analytics.

    Args:
        source: Market data source (real ``yfinance`` or injected test double).
        min_periods: Minimum number of observations required to compute
            statistics; shorter series yield ``NaN``-sanitised values.
    """

    TRADING_DAYS = 252
    # Beklenen getiri/kovaryans tahmini için tutulan en uzun geçmiş (~5 yıl).
    MAX_HISTORY_DAYS = 252 * 5

    def __init__(
        self,
        source: MarketDataSource,
        min_periods: int = 20,
        cache_ttl_seconds: int = 300,
    ) -> None:
        self._source = source
        self._min_periods = min_periods
        self._ttl = cache_ttl_seconds
        # symbol-tuple -> (monotonic timestamp, snapshots)
        self._cache: dict[tuple[str, ...], tuple[float, dict[str, MarketSnapshot]]] = {}

    async def fetch_snapshots(self, symbols: list[str]) -> dict[str, MarketSnapshot]:
        """Download and analyse each requested symbol.

        Args:
            symbols: List of ticker symbols in Yahoo Finance format.

        Returns:
            Mapping of symbol → :class:`MarketSnapshot` containing last price,
            momentum and annualised volatility for every symbol that provided
            enough data.
        """
        key = tuple(sorted(set(symbols)))
        now = asyncio.get_running_loop().time()
        cached = self._cache.get(key)
        if cached is not None and (now - cached[0]) < self._ttl:
            logger.debug("market_cache_hit", symbols=symbols, age=round(now - cached[0], 1))
            return cached[1]

        histories = await self._source.download_history(symbols)
        snapshots: dict[str, MarketSnapshot] = {}
        for sym in symbols:
            frame = self._normalise(histories.get(sym, pd.DataFrame()))
            if frame is None:
                logger.warning("symbol_skipped", symbol=sym, reason="insufficient_data")
                continue
            snapshots[sym] = self._analyse(sym, frame)

        self._cache[key] = (now, snapshots)
        return snapshots

    def invalidate_cache(self, symbols: list[str] | None = None) -> None:
        """Drop cached snapshots (all, or only for the given symbols)."""
        if symbols is None:
            self._cache.clear()
            return
        self._cache.pop(tuple(sorted(set(symbols))), None)

    # -- internals -----------------------------------------------------------

    @staticmethod
    def _normalise(frame: pd.DataFrame) -> pd.DataFrame | None:
        """Select 'Close', drop NaN rows, require enough samples."""
        if frame is None or frame.empty:
            return None
        if "Close" not in frame.columns:
            return None
        clean = frame[["Close"]].dropna()
        clean = clean[clean["Close"] > 0]
        return clean

    def _analyse(self, symbol: str, frame: pd.DataFrame) -> MarketSnapshot:
        """Compute price analytics from a cleaned OHLC frame."""
        closes = frame["Close"]
        returns = closes.pct_change().dropna()

        last_price = float(closes.iloc[-1])
        # 1-month momentum: 21 trading days ago -> today.
        window = min(21, len(closes) - 1)
        momentum = (
            (float(closes.iloc[-1]) / float(closes.iloc[-window - 1]) - 1.0)
            if window >= 1 and window < len(closes)
            else 0.0
        )
        # Annualised volatility from daily standard deviation.
        vol = (
            float(returns.std()) * math.sqrt(self.TRADING_DAYS)
            if len(returns) >= self._min_periods
            else 0.0
        )

        daily_returns = {
            idx.date().isoformat(): float(value)
            for idx, value in returns.tail(self.MAX_HISTORY_DAYS).items()
        }
        logger.debug(
            "symbol_analysed",
            symbol=symbol,
            last_price=last_price,
            momentum_1m=momentum,
            volatility=round(vol, 4),
        )
        return MarketSnapshot(
            ticker=symbol,
            last_price=last_price,
            momentum_1m=momentum,
            volatility_annualized=vol,
            daily_returns=daily_returns,
        )

    # -- convenience for Markowitz -------------------------------------------

    def build_returns_frame(self, snapshots: dict[str, MarketSnapshot]) -> pd.DataFrame:
        """Align per-ticker daily returns into a single ``ticker x date`` frame.

        Tickers that report no returns are dropped; dates are unioned (pandas
        aligns on the index), so the downstream Markowitz routine only sees
        observations where every remaining asset traded.

        Returns:
            A DataFrame indexed by date whose columns are tickers.
        """
        series: dict[str, pd.Series] = {}
        for sym, snap in snapshots.items():
            if not snap.daily_returns:
                continue
            idx = pd.to_datetime(list(snap.daily_returns.keys()))
            series[sym] = pd.Series(list(snap.daily_returns.values()), index=idx)
        if not series:
            return pd.DataFrame()
        frame = pd.DataFrame(series).dropna(how="all")
        return frame


__all__ = ["MarketService", "MarketDataSource", "MarketSnapshot"]
