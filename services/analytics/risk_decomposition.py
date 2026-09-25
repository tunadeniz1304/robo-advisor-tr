"""Where does the portfolio's risk come from?

Two complementary views on the current holdings (market-value weights):

1. **Asset-class decomposition** — Euler risk contributions
   ``RC_i = w_i (Σw)_i / σ_p`` (they sum to σ_p), aggregated by asset class and
   reported as shares of total volatility. A class can hold 10 % of the money
   and carry 40 % of the risk.
2. **Factor model** — weekly OLS of the portfolio's (hypothetical, current
   weights) returns on three observable Turkish macro factors:

   * ``bist``  — BIST 100 weekly return (equity beta),
   * ``usdtry`` — USDTRY weekly change (FX sensitivity: gold, eurobond, FX),
   * ``faiz``  — TL bond fund minus money-market return, i.e. the duration
     return that moves inversely with TL rates (rate sensitivity).

   Variance is split into factor contributions ``β_k Cov(f_k, βᵀf) / Var(r)``
   (they sum to R²) and the idiosyncratic remainder ``1 − R²``.

Weekly data dampen non-synchronous closes (funds price at a different time
than BIST). Pure functions; the endpoint supplies the data.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from services.market_data.universe import asset_class_of

TRADING_WEEKS = 52
FACTOR_LABELS = {
    "bist": "BIST 100 (hisse piyasası)",
    "usdtry": "USDTRY (kur)",
    "faiz": "TL faiz (tahvil − para piyasası)",
}


def class_risk_contributions(weights: pd.Series, cov: pd.DataFrame) -> dict[str, Any]:
    """Euler volatility contributions by instrument and asset class.

    Args:
        weights: Portfolio weights of risky positions (cash excluded, may sum < 1).
        cov: Annualised covariance of the same symbols.
    """
    cols = [c for c in weights.index if c in cov.columns]
    w = weights[cols].to_numpy(dtype=float)
    sigma = cov.loc[cols, cols].to_numpy(dtype=float)
    var = float(w @ sigma @ w)
    vol = float(np.sqrt(max(var, 0.0)))
    rc = w * (sigma @ w) / vol if vol > 0 else np.zeros_like(w)
    by_class: dict[str, dict[str, float]] = {}
    for sym, wi, ri in zip(cols, w, rc, strict=True):
        c = by_class.setdefault(asset_class_of(sym), {"weight": 0.0, "risk": 0.0})
        c["weight"] += float(wi)
        c["risk"] += float(ri)
    return {
        "volatility": vol,
        "instruments": {
            s: {"weight": float(wi), "risk_share": float(ri / vol) if vol > 0 else 0.0}
            for s, wi, ri in zip(cols, w, rc, strict=True)
        },
        "classes": {
            c: {
                "weight": v["weight"],
                "risk_share": v["risk"] / vol if vol > 0 else 0.0,
                "risk_to_weight": (v["risk"] / vol) / v["weight"]
                if vol > 0 and v["weight"]
                else 0.0,
            }
            for c, v in sorted(by_class.items(), key=lambda kv: -kv[1]["risk"])
        },
    }


def factor_decomposition(portfolio: pd.Series, factors: pd.DataFrame) -> dict[str, Any]:
    """OLS of periodic portfolio returns on factor returns (with intercept)."""
    data = pd.concat([portfolio.rename("r"), factors], axis=1).dropna()
    names = list(factors.columns)
    if data.shape[0] < len(names) + 10:
        raise ValueError("Faktör regresyonu için yeterli gözlem yok.")
    y = data["r"].to_numpy(dtype=float)
    x = np.column_stack([np.ones(len(data)), data[names].to_numpy(dtype=float)])
    coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    resid = y - x @ coef
    n, k = x.shape
    s2 = float(resid @ resid) / (n - k)
    cov_b = s2 * np.linalg.inv(x.T @ x)
    se = np.sqrt(np.diag(cov_b))
    var_y = float(np.var(y, ddof=1))
    fitted_factor = data[names].to_numpy(dtype=float) @ coef[1:]
    contributions = {}
    for j, name in enumerate(names):
        cov_fk = float(np.cov(data[name].to_numpy(dtype=float), fitted_factor, ddof=1)[0, 1])
        contributions[name] = coef[j + 1] * cov_fk / var_y if var_y > 0 else 0.0
    r2 = float(sum(contributions.values()))
    return {
        "observations": int(n),
        "start": str(pd.Timestamp(data.index[0]).date()),
        "end": str(pd.Timestamp(data.index[-1]).date()),
        "alpha_annual": float(coef[0]) * TRADING_WEEKS,
        "betas": {
            name: {
                "beta": float(coef[j + 1]),
                "t_stat": float(coef[j + 1] / se[j + 1]) if se[j + 1] > 0 else 0.0,
                "variance_share": float(contributions[name]),
                "label": FACTOR_LABELS.get(name, name),
            }
            for j, name in enumerate(names)
        },
        "r_squared": r2,
        "idiosyncratic_share": max(0.0, 1.0 - r2),
    }


def weekly_factors(prices: pd.DataFrame) -> pd.DataFrame:
    """Weekly factor returns from closes of XU100.IS, USDTRY, TL_TAHVIL, TL_PPF."""
    weekly = prices.resample("W-FRI").last().pct_change(fill_method=None).iloc[1:]
    return pd.DataFrame(
        {
            "bist": weekly["XU100.IS"],
            "usdtry": weekly["USDTRY"],
            "faiz": weekly["TL_TAHVIL"] - weekly["TL_PPF"],
        }
    ).dropna()


__all__ = [
    "FACTOR_LABELS",
    "class_risk_contributions",
    "factor_decomposition",
    "weekly_factors",
]
