"""Moment estimators: Ledoit-Wolf covariance and shrunk expected returns.

* :func:`ledoit_wolf` — Ledoit & Wolf (2004) shrinkage of the sample
  covariance toward a scaled identity (closed form, no sklearn needed).
* :func:`expected_returns` — sample means shrunk toward either the grand
  mean (James-Stein) or a CAPM prior ``rf + β (μ_m − rf)``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from services.portfolio_service import james_stein_means

TRADING_DAYS = 252


def ledoit_wolf(
    returns: pd.DataFrame, periods_per_year: int = TRADING_DAYS
) -> tuple[np.ndarray, float]:
    """Ledoit-Wolf shrunk covariance (annualised) and the shrinkage intensity.

    Args:
        returns: Returns (rows=observations, cols=assets), no NaNs.
        periods_per_year: Annualisation factor.

    Returns:
        ``(covariance, delta)`` where ``delta ∈ [0, 1]`` is the weight on the
        scaled-identity target.
    """
    x = returns.to_numpy(dtype=float)
    t, n = x.shape
    if t < 2:
        raise ValueError("Kovaryans için en az 2 gözlem gerekir.")
    x = x - x.mean(axis=0)
    sample = x.T @ x / t
    mu = float(np.trace(sample)) / n
    target = mu * np.eye(n)
    d2 = float(np.sum((sample - target) ** 2))
    b2_bar = 0.0
    for row in x:
        b2_bar += float(np.sum((np.outer(row, row) - sample) ** 2))
    b2_bar /= t * t
    b2 = min(b2_bar, d2)
    delta = b2 / d2 if d2 > 0 else 1.0
    shrunk = delta * target + (1.0 - delta) * sample
    return shrunk * periods_per_year, float(delta)


def capm_prior(
    returns: pd.DataFrame, market: pd.Series, rf: float, periods_per_year: int = TRADING_DAYS
) -> np.ndarray:
    """CAPM implied returns ``rf + β_i (μ_m − rf)`` (annualised)."""
    aligned = returns.join(market.rename("__mkt__"), how="inner").dropna()
    mkt = aligned.pop("__mkt__").to_numpy()
    var_m = float(np.var(mkt, ddof=1))
    betas = np.array(
        [
            float(np.cov(aligned[c].to_numpy(), mkt, ddof=1)[0, 1]) / var_m if var_m > 0 else 1.0
            for c in aligned.columns
        ]
    )
    mu_m = float(np.mean(mkt)) * periods_per_year
    return rf + betas * (mu_m - rf)


def expected_returns(
    returns: pd.DataFrame,
    *,
    method: str = "james_stein",
    rf: float = 0.0,
    market: pd.Series | None = None,
    cov: np.ndarray | None = None,
    periods_per_year: int = TRADING_DAYS,
    prior_weight: float = 0.5,
) -> np.ndarray:
    """Annualised expected returns with shrinkage.

    Args:
        returns: Returns frame.
        method: ``james_stein`` (toward grand mean) or ``capm`` (blend of the
            sample mean with the CAPM prior using ``prior_weight``).
        rf: Annual risk-free rate (CAPM).
        market: Market return series (CAPM).
        cov: Annualised covariance (James-Stein noise estimate).
        periods_per_year: Annualisation factor.
        prior_weight: Weight on the CAPM prior in ``[0, 1]``.
    """
    sample = returns.mean().to_numpy(dtype=float) * periods_per_year
    if method == "capm" and market is not None:
        prior = capm_prior(returns, market, rf, periods_per_year)
        return prior_weight * prior + (1.0 - prior_weight) * sample
    if cov is None:
        cov = np.atleast_2d(np.cov(returns.to_numpy(dtype=float), rowvar=False)) * periods_per_year
    return james_stein_means(sample, cov, int(returns.shape[0] * TRADING_DAYS / periods_per_year))


__all__ = ["capm_prior", "expected_returns", "ledoit_wolf"]
