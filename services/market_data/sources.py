"""Market data sources: offline snapshot, live Yahoo Finance and the
live → snapshot chain (EVDS/TEFAS/TCMB are fetched by scripts/fetch_real_data.py).

Every price source satisfies :class:`services.market_service.MarketDataSource`
(``download_history(symbols) -> {symbol: DataFrame[Close]}``) keyed by
**instrument** symbols of :mod:`services.market_data.universe`, so the
existing :class:`~services.market_service.MarketService` works unchanged.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pandas as pd

from core.config import BASE_DIR
from core.logging import get_logger
from services.market_data.derive import derive_instruments
from services.market_data.universe import BY_SYMBOL, UNDERLYING_YAHOO

logger = get_logger("otonom.market.sources")

SNAPSHOT_DIR = BASE_DIR / "data" / "snapshots"
PRICES_FILE = "prices.parquet"
LEGACY_PRICES_FILE = "prices.csv.gz"
MACRO_FILE = "macro.csv"
MACRO_MANUAL_FILE = "macro_manual.csv"
META_FILE = "meta.json"


def read_macro_csv(path: Path) -> pd.DataFrame:
    """Read a monthly macro CSV (``#`` comment lines allowed)."""
    frame = pd.read_csv(path, comment="#", parse_dates=["date"], index_col="date")
    return frame.sort_index()


def _to_frames(panel: pd.DataFrame, symbols: list[str]) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        if sym in panel.columns:
            series = panel[sym].dropna()
            out[sym] = pd.DataFrame(
                {"Close": series.to_numpy()}, index=pd.DatetimeIndex(series.index)
            )
        else:
            out[sym] = pd.DataFrame()
    return out


class SnapshotSource:
    """Deterministic offline source backed by ``data/snapshots``."""

    name = "snapshot"

    def __init__(self, directory: Path | None = None) -> None:
        self._dir = directory or SNAPSHOT_DIR
        self._panel: pd.DataFrame | None = None
        self._macro: pd.DataFrame | None = None

    @property
    def directory(self) -> Path:
        return self._dir

    def panel(self) -> pd.DataFrame:
        """Full instrument close panel (cached in memory)."""
        if self._panel is None:
            path = self._dir / PRICES_FILE
            legacy = self._dir / LEGACY_PRICES_FILE
            if path.is_file():
                frame = pd.read_parquet(path)
                frame.index = pd.DatetimeIndex(frame.index, name="date")
            elif legacy.is_file():
                frame = pd.read_csv(legacy, parse_dates=["date"], index_col="date")
            else:
                raise RuntimeError(f"Snapshot bulunamadı: {path}")
            self._panel = frame.sort_index()
        return self._panel

    def macro(self) -> pd.DataFrame:
        """Monthly macro frame (TUFE, POLICY_RATE, …)."""
        if self._macro is None:
            path = self._dir / MACRO_FILE
            if not path.is_file():
                path = self._dir / MACRO_MANUAL_FILE
            self._macro = read_macro_csv(path)
        return self._macro

    def meta(self) -> dict[str, Any]:
        path = self._dir / META_FILE
        if path.is_file():
            meta: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            return meta
        return {}

    async def download_history(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        return _to_frames(self.panel(), symbols)


class LiveYahooSource:
    """Live Yahoo Finance source that also derives proxy instruments."""

    name = "live"

    def __init__(
        self,
        macro_provider: SnapshotSource | None = None,
        period: str = "10y",
        ttl_seconds: float = 900.0,
    ) -> None:
        self._period = period
        self._macro_provider = macro_provider or SnapshotSource()
        self._ttl = ttl_seconds
        self._cache: tuple[float, pd.DataFrame] | None = None
        self._lock = asyncio.Lock()

    def _download_raw(self, yahoo_symbols: list[str]) -> pd.DataFrame:
        import yfinance as yf

        data = yf.download(
            tickers=" ".join(yahoo_symbols),
            period=self._period,
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        closes: dict[str, pd.Series] = {}
        for sym in yahoo_symbols:
            try:
                col = data[sym]["Close"] if len(yahoo_symbols) > 1 else data["Close"]
            except KeyError:
                continue
            series = col.dropna()
            if not series.empty:
                closes[sym] = series
        frame = pd.DataFrame(closes)
        frame.index = pd.DatetimeIndex(frame.index).tz_localize(None).normalize()
        return frame

    def fetch_panel(self, yahoo_symbols: list[str] | None = None) -> pd.DataFrame:
        """Blocking: download underlyings and return the derived panel."""
        symbols = list(dict.fromkeys([*(yahoo_symbols or UNDERLYING_YAHOO), "USDTRY=X"]))
        raw = self._download_raw(symbols)
        if raw.empty:
            raise RuntimeError("Yahoo Finance boş yanıt döndü.")
        try:
            macro = self._macro_provider.macro()
        except Exception:  # noqa: BLE001
            macro = pd.DataFrame()
        panel = derive_instruments(raw, macro)
        extra = [s for s in raw.columns if s not in panel.columns and s not in UNDERLYING_YAHOO]
        for sym in extra:
            panel[sym] = raw[sym]
        return panel

    async def _panel(self, extra: list[str]) -> pd.DataFrame:
        loop = asyncio.get_running_loop()
        async with self._lock:
            if self._cache is not None and loop.time() - self._cache[0] < self._ttl:
                panel = self._cache[1]
                if all(s in panel.columns for s in extra):
                    return panel
            symbols = [*UNDERLYING_YAHOO, *extra]
            try:
                panel = await asyncio.to_thread(self.fetch_panel, symbols)
            except Exception as exc:
                logger.warning("live_market_download_failed", error_type=type(exc).__name__)
                raise RuntimeError("Canlı piyasa verisi indirilemedi.") from exc
            self._cache = (loop.time(), panel)
            return panel

    async def download_history(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        extra = [s for s in symbols if s not in BY_SYMBOL]
        panel = await self._panel(extra)
        return _to_frames(panel, symbols)


class ChainedSource:
    """Try the live source first; fall back to the snapshot per symbol.

    ``last_source`` reports ``live`` / ``snapshot`` / ``mixed`` for the UI
    badge ("Veri: Canlı / Önbellek") and the readiness probe.
    """

    name = "chain"

    def __init__(self, live: Any | None, snapshot: SnapshotSource, mode: str = "auto") -> None:
        self._live = live
        self._snapshot = snapshot
        self._mode = mode
        self.last_source = "snapshot"
        self.last_error: str | None = None

    @property
    def snapshot(self) -> SnapshotSource:
        return self._snapshot

    async def download_history(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        from core.metrics import DATA_SOURCE

        live_frames: dict[str, pd.DataFrame] = {}
        if self._live is not None and self._mode in {"auto", "live"}:
            try:
                live_frames = await self._live.download_history(symbols)
                self.last_error = None
            except RuntimeError as exc:
                self.last_error = str(exc)
                if self._mode == "live":
                    raise
        snap_frames = await self._snapshot.download_history(symbols)
        result: dict[str, pd.DataFrame] = {}
        used_live = used_snap = False
        for sym in symbols:
            frame = live_frames.get(sym)
            if frame is not None and not frame.empty:
                result[sym] = frame
                used_live = True
            else:
                result[sym] = snap_frames.get(sym, pd.DataFrame())
                used_snap = used_snap or not result[sym].empty
        self.last_source = (
            "mixed" if used_live and used_snap else ("live" if used_live else "snapshot")
        )
        DATA_SOURCE.set(1 if used_live else 0)
        logger.info("market_data_source", source=self.last_source, symbols=len(symbols))
        return result


__all__ = [
    "ChainedSource",
    "LiveYahooSource",
    "MACRO_FILE",
    "META_FILE",
    "PRICES_FILE",
    "SNAPSHOT_DIR",
    "SnapshotSource",
    "read_macro_csv",
]
