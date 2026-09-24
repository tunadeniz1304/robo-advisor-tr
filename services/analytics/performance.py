"""Performance analytics: tear sheet metrics, TWR and money-weighted return.

Pure functions over daily return/value series (unit-testable with known
series):

* :func:`tear_sheet` — CAGR, volatility, Sharpe, Sortino, Calmar, max
  drawdown, historical and parametric VaR/CVaR (95 %), best/worst month,
  hit rate and optional benchmark comparison (beta, tracking error,
  information ratio, excess CAGR).

  Sharpe and Sortino use the ex-post definition on periodic returns:
  ``(mean(r) − r_f,p) · N / σ_ann`` with the *same* annual risk-free rate the
  caller passes (the real TL policy rate in the app); they are therefore
  defined for any period length. CAGR is not reported for periods shorter
  than one year when the caller supplies the period length in ``years``.
* :func:`time_weighted_return` — chain-linked daily returns net of
  external cash flows.
* :func:`xirr` — money-weighted return (annualised IRR of dated flows).
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import brentq

TRADING_DAYS = 252
Z95 = 1.6448536269514722


def _cagr(series: pd.Series, periods_per_year: int) -> float:
    growth = float((1.0 + series).prod())
    years = len(series) / periods_per_year
    return growth ** (1.0 / years) - 1.0 if years > 0 and growth > 0 else -1.0


def max_drawdown(returns: pd.Series) -> tuple[float, pd.Series]:
    wealth = (1.0 + returns).cumprod()
    dd = wealth / wealth.cummax() - 1.0
    return float(dd.min()), dd


def tear_sheet(
    returns: pd.Series,
    *,
    risk_free_rate: float = 0.0,
    benchmark: pd.Series | None = None,
    periods_per_year: int = TRADING_DAYS,
    years: float | None = None,
) -> dict[str, Any]:
    """Tear sheet metrics of a periodic simple return series.

    Args:
        returns: Periodic simple returns.
        risk_free_rate: Annual risk-free rate (Sharpe/Sortino).
        benchmark: Optional benchmark returns on the same index.
        periods_per_year: Periods per year (252 daily, 12 monthly).
        years: Calendar length of the period. When given, CAGR uses it and is
            ``None`` below one year; otherwise ``len(returns)/periods_per_year``.
    """
    r = returns.replace([np.inf, -np.inf], np.nan).dropna()
    if r.shape[0] < 2:
        return {"observations": int(r.shape[0]), "risk_free_rate": risk_free_rate}
    growth = float((1.0 + r).prod())
    if years is None:
        cagr: float | None = _cagr(r, periods_per_year)
    elif years >= 1.0 and growth > 0:
        cagr = growth ** (1.0 / years) - 1.0
    else:
        cagr = None
    vol = float(r.std(ddof=1)) * math.sqrt(periods_per_year)
    rf_p = (1.0 + risk_free_rate) ** (1.0 / periods_per_year) - 1.0
    excess_ann = (float(r.mean()) - rf_p) * periods_per_year
    downside = r[r < rf_p] - rf_p
    dvol = float(np.sqrt((downside**2).sum() / len(r))) * math.sqrt(periods_per_year)
    mdd, _ = max_drawdown(r)
    q = float(np.quantile(r, 0.05))
    tail = r[r <= q]
    mu, sd = float(r.mean()), float(r.std(ddof=1))
    monthly = (
        (1.0 + r).groupby(pd.Grouper(freq="ME")).prod() - 1.0
        if isinstance(r.index, pd.DatetimeIndex)
        else pd.Series(dtype=float)
    )
    out: dict[str, Any] = {
        "observations": int(r.shape[0]),
        "start": str(r.index[0].date()) if isinstance(r.index, pd.DatetimeIndex) else None,
        "end": str(r.index[-1].date()) if isinstance(r.index, pd.DatetimeIndex) else None,
        "total_return": growth - 1.0,
        "cagr": cagr,
        "volatility": vol,
        "sharpe": excess_ann / vol if vol > 1e-12 else 0.0,
        "sortino": excess_ann / dvol if dvol > 1e-12 else 0.0,
        "calmar": cagr / abs(mdd) if cagr is not None and mdd < -1e-12 else None,
        "max_drawdown": mdd,
        "var_95_hist": -q,
        "cvar_95_hist": -float(tail.mean()) if tail.size else -q,
        "var_95_param": -(mu - Z95 * sd),
        "cvar_95_param": -(mu - sd * math.exp(-(Z95**2) / 2) / (math.sqrt(2 * math.pi) * 0.05)),
        "best_month": float(monthly.max()) if monthly.size else None,
        "worst_month": float(monthly.min()) if monthly.size else None,
        "hit_rate": float((r > 0).mean()),
        "risk_free_rate": risk_free_rate,
    }
    if benchmark is not None:
        b = benchmark.reindex(r.index).fillna(0.0)
        cov = float(np.cov(r, b, ddof=1)[0, 1])
        var_b = float(b.var(ddof=1))
        active = r - b
        te = float(active.std(ddof=1)) * math.sqrt(periods_per_year)
        b_growth = float((1.0 + b).prod())
        if years is None:
            b_cagr: float | None = _cagr(b, periods_per_year)
        elif years >= 1.0 and b_growth > 0:
            b_cagr = b_growth ** (1.0 / years) - 1.0
        else:
            b_cagr = None
        active_ann = float(active.mean()) * periods_per_year
        out["benchmark"] = {
            "cagr": b_cagr,
            "total_return": b_growth - 1.0,
            "volatility": float(b.std(ddof=1)) * math.sqrt(periods_per_year),
            "max_drawdown": max_drawdown(b)[0],
            "beta": cov / var_b if var_b > 1e-18 else 0.0,
            "tracking_error": te,
            "information_ratio": active_ann / te if te > 1e-12 else 0.0,
            "excess_cagr": cagr - b_cagr if cagr is not None and b_cagr is not None else None,
        }
    return out


def time_weighted_return(
    values: pd.Series, flows: pd.Series | None = None
) -> tuple[float, pd.Series]:
    """TWR from a value series and external flows (flow at end of day t).

    ``r_t = (V_t − F_t) / V_{t−1} − 1``; returns ``(total_twr, daily_returns)``.
    """
    v = values.astype(float)
    f = (
        flows.reindex(v.index).fillna(0.0) if flows is not None else pd.Series(0.0, index=v.index)
    ).astype(float)
    prev = v.shift(1)
    # Portföy henüz fonlanmamışken (≈0 değer) getiri tanımsızdır; bu günler 0 sayılır.
    floor = max(1.0, float(v.abs().max()) * 1e-6)
    daily = ((v - f) / prev.where(prev > floor) - 1.0).iloc[1:]
    daily = daily.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return float((1.0 + daily).prod() - 1.0), daily


def xirr(cashflows: list[tuple[date, float]]) -> float | None:
    """Annualised money-weighted return (investor view: deposits negative)."""
    if len(cashflows) < 2:
        return None
    flows = sorted(cashflows)
    t0 = flows[0][0]
    times = np.array([(d - t0).days / 365.0 for d, _ in flows])
    amounts = np.array([a for _, a in flows])
    if not (amounts.min() < 0 < amounts.max()):
        return None

    def npv(rate: float) -> float:
        return float(np.sum(amounts / (1.0 + rate) ** times))

    try:
        return float(brentq(npv, -0.99, 100.0, maxiter=500))
    except ValueError:
        return None


__all__ = ["max_drawdown", "tear_sheet", "time_weighted_return", "xirr"]
