"""Market regime detection (boğa / yatay / stres).

Monthly features — BIST 100 return, USDTRY change, policy-rate change and
TÜFE change — are standardised and fitted with ``hmmlearn.GaussianHMM``
(3 states, fixed seed). States are labelled by their mean BIST return
(highest → boğa, lowest → stres). Without hmmlearn a transparent fallback
uses 3-month BIST return and volatility quantiles.

The regime translates into a bounded risk-budget tilt (``±max_tilt`` from
the policy) applied by the optimiser between defensive and BIST classes.
"""

from __future__ import annotations

from typing import Any, cast

import numpy as np
import pandas as pd

from core.logging import get_logger
from core.policy import get_policy
from services.market_data.service import MarketDataService

logger = get_logger("otonom.regime")

LABELS = ("boğa", "yatay", "stres")


async def regime_features(market: MarketDataService, months: int) -> pd.DataFrame:
    rets = await market.returns(["XU100.IS", "USDTRY"], freq="M")
    macro = market.macro()
    feats = pd.DataFrame(
        {
            "bist": rets.get("XU100.IS"),
            "usdtry": rets.get("USDTRY"),
            "rate": macro["POLICY_RATE"].diff().reindex(rets.index)
            if "POLICY_RATE" in macro
            else 0.0,
            "cpi": macro["TUFE"].dropna().pct_change().reindex(rets.index)
            if "TUFE" in macro
            else 0.0,
        }
    )
    return feats.dropna().tail(months)


def _tilt_for(label: str, max_tilt: float) -> float:
    return {"boğa": max_tilt, "stres": -max_tilt}.get(label, 0.0)


def _fallback(feats: pd.DataFrame) -> tuple[str, dict[str, float], str]:
    ret3 = feats["bist"].rolling(3).sum()
    vol3 = feats["bist"].rolling(3).std()
    r, v = float(ret3.iloc[-1]), float(vol3.iloc[-1])
    if v > float(vol3.quantile(0.8)) or r < float(ret3.quantile(0.2)):
        label = "stres"
    elif r > float(ret3.quantile(0.6)):
        label = "boğa"
    else:
        label = "yatay"
    return label, {lab: 1.0 if lab == label else 0.0 for lab in LABELS}, "volatilite_esigi"


async def detect_regime(market: MarketDataService) -> dict[str, Any]:
    """Current regime, probabilities and the resulting tilt."""
    policy = get_policy()
    feats = await regime_features(market, int(policy.regime.get("lookback_months", 120)))
    max_tilt = float(policy.regime.get("max_tilt", 0.05))
    if feats.shape[0] < 24:
        return {"label": "yatay", "tilt": 0.0, "method": "yetersiz_veri", "probabilities": {}}
    method = "hmm"
    try:
        from hmmlearn.hmm import GaussianHMM

        x = ((feats - feats.mean()) / feats.std(ddof=0).replace(0, 1)).to_numpy()
        model = GaussianHMM(
            n_components=int(policy.regime.get("n_states", 3)),
            covariance_type="diag",
            n_iter=200,
            random_state=7,
        )
        model.fit(x)
        states = model.predict(x)
        post = model.predict_proba(x)[-1]
        means = {
            s: float(feats["bist"].to_numpy()[states == s].mean()) if np.any(states == s) else 0.0
            for s in range(model.n_components)
        }
        order = sorted(means, key=lambda s: means[s], reverse=True)
        names = {order[0]: "boğa", order[-1]: "stres"}
        for s in order[1:-1]:
            names[s] = "yatay"
        label = names[int(states[-1])]
        probs: dict[str, float] = {lab: 0.0 for lab in LABELS}
        for s, p in enumerate(post):
            probs[names[s]] += float(p)
    except Exception as exc:  # noqa: BLE001 - hmmlearn yoksa/yakınsamazsa
        logger.info("regime_hmm_unavailable", error_type=type(exc).__name__)
        label, probs, method = _fallback(feats)
    last = feats.iloc[-1]
    return {
        "label": label,
        "tilt": _tilt_for(label, max_tilt),
        "method": method,
        "probabilities": {k: round(v, 4) for k, v in probs.items()},
        "as_of": feats.index[-1].date().isoformat(),
        "features": {k: round(float(v), 6) for k, v in last.items()},
    }


async def cached_regime(container: Any, ttl_seconds: float = 3600.0) -> dict[str, Any]:
    """Regime cached on the container (the HMM fit is not free)."""
    import time

    hit = container.extras.get("regime")
    if hit is not None and time.monotonic() - hit[0] < ttl_seconds:
        return cast(dict[str, Any], hit[1])
    try:
        value = await detect_regime(container.market)
    except Exception as exc:  # noqa: BLE001 - rejim yoksa eğilim uygulanmaz
        logger.warning("regime_failed", error_type=type(exc).__name__)
        value = {"label": "yatay", "tilt": 0.0, "method": "hata", "probabilities": {}}
    container.extras["regime"] = (time.monotonic(), value)
    return value


__all__ = ["LABELS", "cached_regime", "detect_regime", "regime_features"]
