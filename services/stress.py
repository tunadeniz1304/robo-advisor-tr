"""Stress testing laboratory: historical replays and factor shocks.

* **Historical** — replays a stored window (Aug 2018 FX shock, Mar 2020
  COVID, Dec 2021 FX shock, Mar 2025 turmoil) on today's positions using
  each instrument's cumulative return over the window; the path gives the
  in-window maximum drawdown.
* **Factor shocks** — monthly OLS betas of each instrument on four factors
  (BIST return, USDTRY return, gold in USD, policy-rate change) translate
  a shock vector into P&L: ``ΔV = Σ_i V_i Σ_f β_if · shock_f``. Custom
  scenarios combine any factors.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from core.policy import get_policy
from services.market_data.service import MarketDataService

FACTORS = ("bist", "usdtry", "gold", "rate")


async def factor_betas(
    market: MarketDataService, symbols: list[str]
) -> dict[str, dict[str, float]]:
    """Multivariate OLS betas of instruments on the four factors (monthly)."""
    base = await market.returns(["XU100.IS", "USDTRY", "ALTIN_TL"], freq="M")
    rets = await market.returns(symbols, freq="M")
    macro = market.macro()
    factors = pd.DataFrame(
        {
            "bist": base["XU100.IS"],
            "usdtry": base["USDTRY"],
            "gold": (1 + base["ALTIN_TL"]) / (1 + base["USDTRY"]) - 1,
            "rate": macro["POLICY_RATE"].diff().reindex(base.index)
            if "POLICY_RATE" in macro
            else 0.0,
        }
    )
    data = rets.join(factors, how="inner").dropna().tail(120)
    x = np.column_stack([np.ones(len(data)), data[list(FACTORS)].to_numpy()])
    out: dict[str, dict[str, float]] = {}
    for s in symbols:
        if s not in data:
            continue
        coef, *_ = np.linalg.lstsq(x, data[s].to_numpy(), rcond=None)
        out[s] = {f: float(c) for f, c in zip(FACTORS, coef[1:], strict=True)}
    return out


def scenario_catalog() -> dict[str, Any]:
    stress = get_policy().stress
    return {
        "factor": {k: dict(v) for k, v in stress.get("factor_shocks", {}).items()},
        "historical": {k: dict(v) for k, v in stress.get("historical", {}).items()},
    }


async def run_stress(
    market: MarketDataService,
    values: dict[str, float],
    cash: float,
    *,
    scenario: str | None = None,
    custom: dict[str, float] | None = None,
) -> dict[str, Any]:
    """P&L of positions (TL values) under a named or custom scenario."""
    catalog = scenario_catalog()
    total = cash + sum(values.values())
    symbols = [s for s, v in values.items() if v > 0]
    if scenario in catalog["historical"]:
        spec = catalog["historical"][scenario]
        panel = await market.history(symbols)
        window = panel.loc[spec["start"] : spec["end"]]
        if window.shape[0] < 2:
            raise ValueError("Senaryo penceresi için veri yok.")
        cum = window.iloc[-1] / window.iloc[0] - 1.0
        contrib = {s: values[s] * float(cum.get(s, 0.0)) for s in symbols}
        path = cash + (window / window.iloc[0]).mul(pd.Series(values)[symbols], axis=1).sum(axis=1)
        mdd = float((path / path.cummax() - 1).min())
        label, kind = spec.get("label", scenario), "tarihsel"
    else:
        shocks = dict(custom or {})
        if scenario in catalog["factor"]:
            spec = catalog["factor"][scenario]
            shocks = {f: float(spec[f]) for f in FACTORS if f in spec}
            label = spec.get("label", scenario)
        elif custom:
            label = "Özel senaryo"
        else:
            raise ValueError("Bilinmeyen senaryo.")
        betas = await factor_betas(market, symbols)
        contrib = {
            s: values[s] * sum(betas.get(s, {}).get(f, 0.0) * shocks.get(f, 0.0) for f in FACTORS)
            for s in symbols
        }
        mdd = min(0.0, sum(contrib.values()) / total) if total else 0.0
        kind = "faktor"
        spec = {"shocks": shocks}
    pnl = sum(contrib.values())
    return {
        "senaryo": scenario or "ozel",
        "senaryo_adi": label,
        "tur": kind,
        "pnl": round(pnl, 2),
        "pnl_orani": round(pnl / total, 6) if total else 0.0,
        "maks_dusus": round(mdd, 6),
        "katkilar": {s: round(v, 2) for s, v in sorted(contrib.items(), key=lambda kv: kv[1])},
        "toplam_deger": round(total, 2),
        "parametreler": spec,
    }


__all__ = ["FACTORS", "factor_betas", "run_stress", "scenario_catalog"]
