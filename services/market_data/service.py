"""Market data service: the app-wide (DI singleton) access point to prices,
returns and macro data.

It extends :class:`services.market_service.MarketService` (per-symbol
snapshots + TTL cache used by the agents) with panel-level helpers:

    * ``history`` — wide close panel with its own TTL cache,
    * ``returns`` — simple/log, daily/monthly, TL or USD based, nominal or
      real (deflated with TÜFE),
    * ``risk_free_rate`` / ``inflation_yoy`` — from the macro series,
    * ``data_status`` — live vs snapshot badge information,
    * ``persist_prices`` — writes recent live closes into ``price_history``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import numpy as np
import pandas as pd

from core.logging import get_logger
from core.policy import get_policy
from services.market_data.sources import ChainedSource, SnapshotSource
from services.market_service import MarketDataSource, MarketService

logger = get_logger("otonom.market.data")

TRADING_DAYS = 252


def simple_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """Arithmetic returns ``P_t / P_{t-1} − 1``."""
    return prices.pct_change().iloc[1:]


def log_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """Continuously compounded returns ``ln(P_t / P_{t-1})``."""
    return pd.DataFrame(np.log(prices / prices.shift(1))).iloc[1:]


def to_usd(prices: pd.DataFrame, usdtry: pd.Series) -> pd.DataFrame:
    """Convert TL prices into USD terms."""
    fx = usdtry.reindex(prices.index).ffill()
    return prices.div(fx, axis=0)


def deflate(returns_monthly: pd.DataFrame, cpi: pd.Series) -> pd.DataFrame:
    """Real monthly returns: ``(1 + r) / (1 + π) − 1`` with monthly CPI inflation."""
    infl = cpi.dropna().sort_index().pct_change().reindex(returns_monthly.index).fillna(0.0)
    return (1.0 + returns_monthly).div(1.0 + infl, axis=0) - 1.0


class MarketDataService(MarketService):
    """Shared market/macro data service."""

    def __init__(
        self,
        source: MarketDataSource,
        *,
        snapshot: SnapshotSource | None = None,
        cache_ttl_seconds: int = 300,
    ) -> None:
        super().__init__(source, cache_ttl_seconds=cache_ttl_seconds)
        self._snapshot = snapshot or (
            source.snapshot if isinstance(source, ChainedSource) else SnapshotSource()
        )
        self._panel_cache: dict[tuple[str, ...], tuple[float, pd.DataFrame]] = {}

    # -- panels ----------------------------------------------------------------

    async def history(self, symbols: list[str]) -> pd.DataFrame:
        """Wide daily close panel for ``symbols`` (forward-filled, aligned)."""
        key = tuple(sorted(set(symbols)))
        now = asyncio.get_running_loop().time()
        cached = self._panel_cache.get(key)
        if cached is not None and (now - cached[0]) < self._ttl:
            return cached[1]
        frames = await self._source.download_history(list(key))
        series = {
            sym: frame["Close"]
            for sym, frame in frames.items()
            if frame is not None and not frame.empty and "Close" in frame.columns
        }
        panel = pd.DataFrame(series).sort_index().ffill()
        panel = panel.dropna(how="all")
        self._panel_cache[key] = (now, panel)
        return panel

    async def returns(
        self,
        symbols: list[str],
        *,
        freq: str = "D",
        kind: str = "simple",
        currency: str = "TRY",
        real: bool = False,
    ) -> pd.DataFrame:
        """Returns panel with explicit conventions.

        Args:
            symbols: Instrument symbols.
            freq: ``D`` (daily) or ``M`` (month-end).
            kind: ``simple`` or ``log``.
            currency: ``TRY`` or ``USD`` (converted with USDTRY).
            real: Deflate by TÜFE (monthly frequency only).
        """
        symbols = list(dict.fromkeys(symbols))  # yinelenen semboller tek sütun olmalı
        wanted = list(dict.fromkeys(symbols + (["USDTRY"] if currency == "USD" else [])))
        prices = await self.history(wanted)
        if currency == "USD" and "USDTRY" in prices.columns:
            fx = prices["USDTRY"]
            prices = to_usd(prices[[s for s in symbols if s in prices.columns]], fx)
        else:
            prices = prices[[s for s in symbols if s in prices.columns]]
        if freq == "M":
            prices = prices.resample("ME").last()
        rets = log_returns(prices) if kind == "log" else simple_returns(prices)
        if real:
            if freq != "M":
                raise ValueError("Reel getiri yalnızca aylık frekansta hesaplanır.")
            cpi = self.macro().get("TUFE")
            if cpi is not None:
                rets = deflate(rets, cpi)
        return rets.dropna(how="all")

    # -- macro -----------------------------------------------------------------

    def macro(self) -> pd.DataFrame:
        """Monthly macro frame (TUFE, POLICY_RATE, USDTRY)."""
        try:
            return self._snapshot.macro()
        except Exception:  # noqa: BLE001
            return pd.DataFrame()

    def risk_free_rate(self) -> float:
        """Latest annual TL policy rate (fallback: policy file)."""
        macro = self.macro()
        if "POLICY_RATE" in macro.columns and not macro["POLICY_RATE"].dropna().empty:
            return float(macro["POLICY_RATE"].dropna().iloc[-1])
        return float(get_policy().optimization.get("fallback_risk_free_rate", 0.35))

    def risk_free_rate_at(self, when: pd.Timestamp) -> float:
        """Policy rate in force on ``when`` (no look-ahead for backtests)."""
        macro = self.macro()
        if "POLICY_RATE" in macro.columns:
            series = macro["POLICY_RATE"].dropna().sort_index()
            known = series[series.index <= pd.Timestamp(when)]
            if not known.empty:
                return float(known.iloc[-1])
            if not series.empty:
                return float(series.iloc[0])
        return float(get_policy().optimization.get("fallback_risk_free_rate", 0.35))

    def mean_risk_free_rate(self, start: pd.Timestamp, end: pd.Timestamp) -> float:
        """Average policy rate over ``[start, end]`` (test-period Sharpe)."""
        days = pd.date_range(start, end, freq="MS")
        if len(days) == 0:
            return self.risk_free_rate_at(end)
        return float(np.mean([self.risk_free_rate_at(d) for d in days]))

    def inflation_yoy(self) -> float:
        """Latest year-on-year TÜFE inflation."""
        macro = self.macro()
        cpi = macro.get("TUFE")
        if cpi is None or cpi.dropna().shape[0] < 13:
            return float(get_policy().planning.get("default_inflation", 0.25))
        cpi = cpi.dropna()
        return float(cpi.iloc[-1] / cpi.iloc[-13] - 1.0)

    # -- status / persistence ------------------------------------------------------

    def snapshot_meta(self) -> dict[str, Any]:
        """Provenance metadata of the offline snapshot (per series)."""
        try:
            return dict(self._snapshot.meta()) if hasattr(self._snapshot, "meta") else {}
        except Exception:  # noqa: BLE001
            return {}

    async def quality_report(self) -> dict[str, Any]:
        """Data-quality report of the instrument panel currently served."""
        from services.market_data.quality import assess_panel
        from services.market_data.universe import UNIVERSE

        panel = await self.history([s.symbol for s in UNIVERSE])
        return assess_panel(
            panel, as_of=pd.Timestamp.today().normalize(), meta=self.snapshot_meta()
        )

    def data_mode(self) -> dict[str, Any]:
        """Badge semantics: ``gercek`` (live) / ``snapshot`` / ``yaklasik``.

        ``yaklasik`` when a macro series is the manual approximation or an
        instrument is entirely a proxy; spliced proxy periods are listed.
        """
        meta = self.snapshot_meta()
        series = meta.get("series", {})
        macro = meta.get("macro", {})
        proxies = sorted(k for k, v in series.items() if v.get("is_proxy"))
        approx_macro = sorted(k for k, v in macro.items() if v.get("is_proxy"))
        if not macro and meta.get("macro_source") == "manual_approx":
            approx_macro = ["TUFE", "POLICY_RATE"]
        source = getattr(self._source, "last_source", getattr(self._source, "name", "custom"))
        if proxies or approx_macro:
            mode = "yaklasik"
        elif source in {"live", "mixed"}:
            mode = "gercek"
        else:
            mode = "snapshot"
        return {
            "mode": mode,
            "source": source,
            "snapshot_end": meta.get("end"),
            "proxy_series": proxies,
            "approximate_macro": approx_macro,
            "spliced_series": {
                k: v["proxy_until"] for k, v in series.items() if v.get("proxy_until")
            },
            "macro_sources": {k: v.get("source") for k, v in macro.items()},
        }

    def data_status(self) -> dict[str, Any]:
        """Badge info: ``source`` is ``live`` / ``snapshot`` / ``mixed``."""
        source = getattr(self._source, "last_source", getattr(self._source, "name", "custom"))
        meta = self._snapshot.meta() if hasattr(self._snapshot, "meta") else {}
        return {
            "source": source,
            "snapshot_end": meta.get("end"),
            "macro_source": meta.get("macro_source"),
            "last_error": getattr(self._source, "last_error", None),
        }

    async def persist_prices(self, days: int = 30) -> int:
        """Upsert the last ``days`` closes of live data into ``price_history``."""
        from sqlalchemy import select

        from core.database import session_factory
        from models import PriceHistory
        from services.market_data.universe import UNIVERSE

        if getattr(self._source, "last_source", "snapshot") == "snapshot":
            return 0
        panel = await self.history([s.symbol for s in UNIVERSE])
        recent = panel.tail(days)
        written = 0
        async with session_factory() as session:
            existing = {
                (row.symbol, row.date)
                for row in (
                    await session.execute(
                        select(PriceHistory.symbol, PriceHistory.date).where(
                            PriceHistory.date >= recent.index.min().date()
                        )
                    )
                ).all()
            }
            for ts, row in recent.iterrows():
                for sym, value in row.items():
                    if pd.isna(value) or (sym, ts.date()) in existing:
                        continue
                    session.add(
                        PriceHistory(
                            symbol=str(sym), date=ts.date(), close=float(value), source="live"
                        )
                    )
                    written += 1
            await session.commit()
        logger.info("price_history_persisted", rows=written)
        return written


__all__ = [
    "MarketDataService",
    "deflate",
    "log_returns",
    "simple_returns",
    "to_usd",
]
