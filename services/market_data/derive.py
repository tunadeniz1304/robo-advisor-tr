"""Derivation of proxy instrument prices from underlyings and macro data.

* ``TL_PPF``     — money-market fund: daily accrual of the policy rate minus
  the expense ratio, ``P_t = P_{t-1} · (1 + (r − fee))^{Δdays/365}``.
* ``TL_TAHVIL``  — TL bond fund: carry of the policy rate plus a duration
  effect on rate changes, ``R_t = carry − D · Δr`` (D = 2 years).
* ``EUROBOND_TL`` — EMB (USD EM sovereign bonds) × USDTRY.
* ``ALTIN_TL``   — gram gold in TL: ``GC=F × USDTRY / 31.1035``.

All functions are pure and deterministic.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from services.market_data.universe import BY_SYMBOL, UNIVERSE

TROY_OUNCE_GRAMS = 31.1035
BOND_DURATION_YEARS = 2.0
BASE_PRICE = 100.0


def policy_rate_daily(macro: pd.DataFrame, index: pd.DatetimeIndex) -> pd.Series:
    """Forward-fill the monthly policy rate onto a daily index."""
    if macro is None or macro.empty or "POLICY_RATE" not in macro.columns:
        return pd.Series(0.35, index=index)
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


def derive_instruments(raw: pd.DataFrame, macro: pd.DataFrame | None) -> pd.DataFrame:
    """Build the instrument price panel from Yahoo underlyings.

    Args:
        raw: Wide daily close frame keyed by Yahoo symbols.
        macro: Monthly macro frame (``POLICY_RATE`` …), may be ``None``.

    Returns:
        Wide daily close frame keyed by instrument symbols.
    """
    frame = raw.sort_index().ffill()
    out: dict[str, pd.Series] = {}
    usdtry = frame.get("USDTRY=X")
    for spec in UNIVERSE:
        if spec.source != "proxy" and spec.yahoo_symbol in frame.columns:
            out[spec.symbol] = frame[spec.yahoo_symbol]
    if usdtry is not None:
        if "GC=F" in frame.columns:
            out["ALTIN_TL"] = frame["GC=F"] * usdtry / TROY_OUNCE_GRAMS
        if "EMB" in frame.columns:
            out["EUROBOND_TL"] = frame["EMB"] * usdtry
    index = pd.DatetimeIndex(frame.index)
    rate = policy_rate_daily(macro if macro is not None else pd.DataFrame(), index)
    out["TL_PPF"] = money_market_series(rate, BY_SYMBOL["TL_PPF"].expense_ratio)
    out["TL_TAHVIL"] = bond_fund_series(rate, BY_SYMBOL["TL_TAHVIL"].expense_ratio)
    panel = pd.DataFrame(out, index=index)
    ordered = [s.symbol for s in UNIVERSE if s.symbol in panel.columns]
    return panel[ordered]


__all__ = [
    "bond_fund_series",
    "derive_instruments",
    "money_market_series",
    "policy_rate_daily",
]
