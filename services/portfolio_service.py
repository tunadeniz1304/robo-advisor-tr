"""Markowitz Modern Portfolio Theory (MPT) engine — NumPy / SciPy.

Target weights for the (legacy) single-strategy path:

    1. Expected returns: annualised mean of daily returns, **shrunk** toward
       the cross-sectional grand mean (James-Stein) — sample means from short
       windows are too noisy to optimise on directly.
    2. Covariance: annualised sample covariance, Ledoit-Wolf style shrinkage
       toward a scaled identity when requested.
    3. Weights: the **constrained** maximum-Sharpe portfolio solved with
       SLSQP (long-only bounds, full investment) against a real risk-free
       rate (TL policy/money-market rate, not zero). When no asset beats the
       risk-free rate the tangency portfolio is undefined; the engine falls
       back to the minimum-variance portfolio.

The equity ceiling from the risk assessment scales risky weights; the rest is
cash. The richer multi-strategy optimiser lives in
:mod:`services.optimization`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from core.logging import get_logger

logger = get_logger("otonom.portfolio")

# Number of trading days in a year (used to annualise moments).
TRADING_DAYS = 252

# Ridge term added to the covariance diagonal for numerical stability.
_RIDGE = 1e-8


class PortfolioError(ValueError):
    """Raised when Markowitz optimisation cannot produce feasible weights."""


def james_stein_means(sample_means: np.ndarray, cov: np.ndarray, n_obs: int) -> np.ndarray:
    """Shrink sample means toward their grand mean (James-Stein estimator).

    Args:
        sample_means: Annualised sample means (n,).
        cov: Annualised covariance matrix (n, n).
        n_obs: Number of daily observations used.

    Returns:
        Shrunk annualised expected returns (n,).
    """
    n = len(sample_means)
    if n < 3 or n_obs < 2:
        return sample_means
    grand = float(np.mean(sample_means))
    diff = sample_means - grand
    # Ortalamanın örnekleme varyansı ≈ σ²/T (yıllık ölçekte T yıl sayısı).
    years = max(n_obs / TRADING_DAYS, 1e-6)
    noise = float(np.trace(cov)) / n / years
    denom = float(diff @ diff)
    if denom <= 1e-18:
        return sample_means
    shrink = min(1.0, max(0.0, (n - 2) * noise / denom))
    return grand + (1.0 - shrink) * diff


class PortfolioService:
    """Markowitz portfolio construction over daily-return data."""

    # -- public API ----------------------------------------------------------

    def expected_returns(self, returns: pd.DataFrame, shrink: bool = True) -> pd.Series:
        """Annualised expected returns per asset (optionally James-Stein shrunk)."""
        self._validate(returns)
        means = returns.mean().to_numpy(dtype=float) * TRADING_DAYS
        if shrink and returns.shape[1] >= 3:
            cov = self.covariance_matrix(returns).to_numpy(dtype=float)
            means = james_stein_means(means, cov, int(returns.shape[0]))
        return pd.Series(means, index=returns.columns)

    def covariance_matrix(self, returns: pd.DataFrame) -> pd.DataFrame:
        """Annualised sample covariance matrix of daily returns."""
        self._validate(returns)
        values = returns.dropna().to_numpy(dtype=float)
        if values.shape[0] < 2:
            values = returns.fillna(0.0).to_numpy(dtype=float)
        cov = np.atleast_2d(np.cov(values, rowvar=False))
        cov = np.nan_to_num(cov, nan=0.0, posinf=0.0, neginf=0.0)
        return pd.DataFrame(cov * TRADING_DAYS, index=returns.columns, columns=returns.columns)

    def tangency_weights(
        self,
        returns: pd.DataFrame,
        risk_free_rate: float = 0.0,
        max_equity_weight: float = 1.0,
    ) -> dict[str, float]:
        """Constrained long-only maximum-Sharpe weights.

        Args:
            returns: Daily simple returns frame (rows=dates, cols=tickers).
            risk_free_rate: Annualised risk-free rate (e.g. 0.40 for 40 %).
            max_equity_weight: Ceiling on total risky allocation in [0,1].

        Returns:
            Mapping ticker -> weight (non-negative; sums to the ceiling).

        Raises:
            PortfolioError: If returns are empty or degenerate.
        """
        frame = returns.dropna(how="all")
        self._validate(frame)
        if frame.shape[0] < 2 or frame.shape[1] == 0:
            raise PortfolioError("Yeterli getiri verisi yok.")

        mu = self.expected_returns(frame).to_numpy(dtype=float)
        sigma = self.covariance_matrix(frame).to_numpy(dtype=float)
        sigma = sigma + np.eye(len(mu)) * _RIDGE

        if len(mu) == 1:
            weights = np.array([1.0])
        elif np.all(mu <= risk_free_rate):
            logger.info("tangency_undefined_min_variance", rf=risk_free_rate)
            weights = self.min_variance(sigma)
        else:
            weights = self._max_sharpe(mu, sigma, float(risk_free_rate))

        weights = self._apply_equity_cap(weights, max_equity_weight)
        result = {ticker: float(w) for ticker, w in zip(frame.columns, weights, strict=True)}
        self._log_result(result, risk_free_rate)
        return result

    # -- solvers -------------------------------------------------------------

    @staticmethod
    def min_variance(sigma: np.ndarray) -> np.ndarray:
        """Long-only global minimum-variance weights (SLSQP)."""
        n = sigma.shape[0]
        x0 = np.full(n, 1.0 / n)
        res = minimize(
            lambda w: float(w @ sigma @ w),
            x0,
            jac=lambda w: 2.0 * sigma @ w,
            bounds=[(0.0, 1.0)] * n,
            constraints=[{"type": "eq", "fun": lambda w: float(np.sum(w) - 1.0)}],
            method="SLSQP",
            options={"maxiter": 500, "ftol": 1e-12},
        )
        return PortfolioService._clean(res.x if res.success else x0)

    @staticmethod
    def _max_sharpe(mu: np.ndarray, sigma: np.ndarray, rf: float) -> np.ndarray:
        n = len(mu)
        x0 = np.full(n, 1.0 / n)
        excess = mu - rf

        def neg_sharpe(w: np.ndarray) -> float:
            vol = float(np.sqrt(max(w @ sigma @ w, 1e-18)))
            return -float(w @ excess) / vol

        res = minimize(
            neg_sharpe,
            x0,
            bounds=[(0.0, 1.0)] * n,
            constraints=[{"type": "eq", "fun": lambda w: float(np.sum(w) - 1.0)}],
            method="SLSQP",
            options={"maxiter": 500, "ftol": 1e-12},
        )
        if not res.success or not np.all(np.isfinite(res.x)):
            logger.warning("max_sharpe_failed_min_variance", message=str(res.message))
            return PortfolioService.min_variance(sigma)
        return PortfolioService._clean(res.x)

    # -- internal helpers ----------------------------------------------------

    @staticmethod
    def _clean(weights: np.ndarray) -> np.ndarray:
        w = np.where(weights < 1e-6, 0.0, weights)
        total = float(np.sum(w))
        if total <= 1e-12:
            return np.full(len(weights), 1.0 / len(weights))
        return w / total

    @staticmethod
    def _validate(returns: pd.DataFrame) -> None:
        """Guard against malformed returns input."""
        if returns is None or returns.empty:
            raise PortfolioError("Getiri verisi boş.")
        if returns.shape[1] == 0:
            raise PortfolioError("Getiri verisinde varlık sütunu yok.")

    @staticmethod
    def _apply_equity_cap(weights: np.ndarray, max_equity: float) -> np.ndarray:
        """Scale risky weights to respect ``max_equity`` (cash takes the rest)."""
        total_risk = float(np.sum(weights))
        cap = max(0.0, min(float(max_equity), 1.0))
        if total_risk <= cap:
            return weights
        scale = cap / total_risk if total_risk > 0 else 0.0
        return weights * scale

    @staticmethod
    def _log_result(result: dict[str, float], rf: float) -> None:
        logger.info(
            "markowitz_weights",
            weights={k: round(v, 4) for k, v in result.items()},
            risk_weight_sum=round(sum(result.values()), 4),
            risk_free_rate=rf,
        )


__all__ = ["PortfolioError", "PortfolioService", "TRADING_DAYS", "james_stein_means"]
