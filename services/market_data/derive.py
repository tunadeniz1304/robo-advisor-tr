"""Derived instrument prices and the documented proxies used for splicing.

* :func:`derive_instruments` — the live panel from Yahoo underlyings:
  directly quoted instruments plus gram gold in TL
  (``GC=F × USDTRY / 31.1035``, a unit conversion of two real series). Fund
  instruments are *not* derived here: their prices are real TEFAS NAVs from
  the snapshot (the live → snapshot chain fills them in per symbol).
* Proxies — only used by ``scripts/fetch_real_data.py`` to extend a real
  TEFAS series before its first available date (TEFAS serves five years):

  * ``money_market`` — daily accrual of the policy rate minus the fee,
    ``P_t = P_{t-1} · (1 + (r − fee))^{Δdays/365}``;
  * ``bond`` — carry of the policy rate minus duration × rate change,
    ``R_t = carry − D · Δr`` (D = 2 years);
  * ``eurobond`` — EMB (USD EM sovereign bond ETF) × USDTRY.

All functions are pure and deterministic.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from services.market_data.universe import BY_SYMBOL, UNIVERSE

TROY_OUNCE_GRAMS = 31.1035
BOND_DURATION_YEARS = 2.0
BASE_PRICE = 100.0
SPLICE_METHODS = ("money_market", "bond", "eurobond")


def policy_rate_daily(macro: pd.DataFrame, index: pd.DatetimeIndex) -> pd.Series:
    """Forward-fill the (monthly or step) policy rate onto a daily index."""
    if macro is None or macro.empty or "POLICY_RATE" not in macro.columns:
        raise ValueError("Politika faizi serisi yok; vekil üretilemez.")
    monthly = macro["POLICY_RATE"].dropna().sort_index()
    daily = monthly.reindex(monthly.index.union(index)).ffill().bfill()
    return daily.reindex(index)


def money_market_series(rate: pd.Series, fee: float) -> pd.Series:
    """Accrual index of a TL money-market fund."""
    days = rate.index.to_series().diff().dt.days.fillna(0).to_numpy()
    growth = np.power(1.0 + np.maximum(rate.to_numpy() - fee, -0.99), days / 365.0)
    return pd.Series(BASE_PRICE * np.cumprod(growth), index=rate.index)


def bond_fund_series(
    rate: pd.Series, fee: float, duration: float = BOND_DURATION_YEARS
) -> pd.Series:
    """TL bond fund proxy: carry minus duration × rate change."""
    days = rate.index.to_series().diff().dt.days.fillna(0).to_numpy()
    carry = np.power(1.0 + np.maximum(rate.to_numpy() - fee, -0.99), days / 365.0) - 1.0
    d_rate = np.nan_to_num(np.diff(rate.to_numpy(), prepend=rate.to_numpy()[0]))
    ret = carry - duration * d_rate
    return pd.Series(BASE_PRICE * np.cumprod(1.0 + ret), index=rate.index)


def proxy_series(method: str, symbol: str, raw: pd.DataFrame, macro: pd.DataFrame) -> pd.Series:
    """Documented proxy price series for ``symbol`` on ``raw.index``.

    Args:
        method: One of :data:`SPLICE_METHODS`.
        symbol: Instrument symbol (for its expense ratio).
        raw: Daily Yahoo underlyings (needs ``USDTRY=X`` and ``EMB`` for
            ``eurobond``).
        macro: Frame with ``POLICY_RATE`` (fraction).
    """
    index = pd.DatetimeIndex(raw.index)
    fee = BY_SYMBOL[symbol].expense_ratio
    if method == "money_market":
        return money_market_series(policy_rate_daily(macro, index), fee)
    if method == "bond":
        return bond_fund_series(policy_rate_daily(macro, index), fee)
    if method == "eurobond":
        frame = raw.sort_index().ffill()
        return (frame["EMB"] * frame["USDTRY=X"]).rename(symbol)
    raise ValueError(f"Bilinmeyen vekil yöntemi: {method}")


def derive_instruments(raw: pd.DataFrame, macro: pd.DataFrame | None = None) -> pd.DataFrame:
    """Live instrument panel from Yahoo underlyings (no fund proxies).

    Args:
        raw: Wide daily close frame keyed by Yahoo symbols.
        macro: Unused; kept for call-site compatibility.

    Returns:
        Wide daily close frame keyed by instrument symbols.
    """
    del macro
    frame = raw.sort_index().ffill()
    out: dict[str, pd.Series] = {}
    for spec in UNIVERSE:
        if spec.source == "yfinance" and spec.yahoo_symbol in frame.columns:
            out[spec.symbol] = frame[spec.yahoo_symbol]
    usdtry = frame.get("USDTRY=X")
    if usdtry is not None and "GC=F" in frame.columns:
        out["ALTIN_TL"] = frame["GC=F"] * usdtry / TROY_OUNCE_GRAMS
    panel = pd.DataFrame(out, index=pd.DatetimeIndex(frame.index))
    ordered = [s.symbol for s in UNIVERSE if s.symbol in panel.columns]
    return panel[ordered]


__all__ = [
    "SPLICE_METHODS",
    "TROY_OUNCE_GRAMS",
    "bond_fund_series",
    "derive_instruments",
    "money_market_series",
    "policy_rate_daily",
    "proxy_series",
]
