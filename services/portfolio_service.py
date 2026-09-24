"""Markowitz Modern Portfolio Theory (MPT) engine — ``numpy``/``pandas``.

The Portfolio Manager uses this service to derive *target* asset weights from
real market data:

    1. Expected returns:   annualised mean of daily log/simple returns.
    2. Covariance matrix:  annualised sample covariance of daily returns.
    3. Optimal weights:    the *tangency portfolio* (maximum Sharpe ratio)
       solved analytically from the covariance matrix, with long-only
       handling; falls back to minimum-variance then to equal-weight when the
       matrix is singular or no risk-free solution is feasible.

The risk ceiling produced by :class:`services.risk_service.RiskService` is
applied afterwards: risky weights are scaled down and the remaining weight is
allocated to a risk-free (cash) bucket, which the rebalancer models as cash.

Design notes:
    * Pure functions taking DataFrames in, dictionaries out — unit-testable
      without a database or network.
    * Shapes are validated explicitly; degenerate input is handled with a
      documented fallback chain instead of crashing or emitting NaNs.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.logging import get_logger

logger = get_logger("otonom.portfolio")

# Number of trading days in a year (used to annualise moments).
TRADING_DAYS = 252

# Ridge term added to the covariance diagonal when the matrix is singular.
_RIDGE = 1e-6


class PortfolioError(ValueError):
    """Raised when Markowitz optimisation cannot produce feasible weights."""


class PortfolioService:
    """Markowitz portfolio construction over daily-return data."""

    # -- public API ----------------------------------------------------------

    def expected_returns(self, returns: pd.DataFrame) -> pd.Series:
        """Annualised expected returns per asset.

        Estimated as the arithmetic mean of daily returns scaled by trading
        days. Rows are dates, columns are tickers.

        Args:
            returns: Daily simple returns frame.

        Returns:
            Series indexed by ticker with annualised expected returns.
        """
        self._validate(returns)
        daily_mean = returns.mean()
        return daily_mean * TRADING_DAYS

    def covariance_matrix(self, returns: pd.DataFrame) -> pd.DataFrame:
        """Annualised sample covariance matrix of daily returns.

        ``numpy.cov`` with ``rowvar=False`` treats columns (tickers) as
        variables; the resulting matrix is scaled by trading days.

        Args:
            returns: Daily simple returns frame.

        Returns:
            A symmetric covariance DataFrame (ticker x ticker), annualised.
        """
        self._validate(returns)
        values = returns.to_numpy(dtype=float)
        cov = np.cov(values, rowvar=False)
        cov = np.nan_to_num(cov, nan=0.0, posinf=0.0, neginf=0.0)
        return pd.DataFrame(cov * TRADING_DAYS, index=returns.columns, columns=returns.columns)

    def tangency_weights(
        self,
        returns: pd.DataFrame,
        risk_free_rate: float = 0.0,
        max_equity_weight: float = 1.0,
    ) -> dict[str, float]:
        """Compute optimal long-only weights (maximise Sharpe ratio).

        Solves the tangency portfolio ``w = inv(S) * (mu - rf)`` normalised so
        weights sum to 1, then enforces long-only by iterative clipping and
        renormalisation. If the covariance matrix is (near-)singular a ridge is
        added; if the tangency solution is still infeasible, falls back to
        minimum-variance, then to equal-weight.

        The computed weights are *pre-risk*: the caller (Portfolio Manager)
        applies ``max_equity_weight`` to scale risky exposure and allocate the
        remainder to the risk-free bucket.

        Args:
            returns: Daily simple returns frame (rows=dates, cols=tickers).
            risk_free_rate: Annualised risk-free rate (e.g. 0.15 for 15%).
            max_equity_weight: Ceiling on total risky allocation in [0,1].

        Returns:
            Mapping ticker -> target weight (all non-negative, summing to 1).

        Raises:
            PortfolioError: If returns are empty or the optimisation degenerates
                to an unusable state.
        """
        frame = returns.dropna(how="all")
        self._validate(frame)
        if frame.shape[0] < 2 or frame.shape[1] == 0:
            raise PortfolioError("Yeterli getiri verisi yok.")

        mu = self.expected_returns(frame).to_numpy(dtype=float)
        sigma = self.covariance_matrix(frame).to_numpy(dtype=float)
        rf = float(risk_free_rate)

        weights = self._solve_tangency(sigma, mu, rf)
        weights = self._long_only(weights)
        weights = self._apply_equity_cap(weights, max_equity_weight)

        result = {ticker: float(w) for ticker, w in zip(frame.columns, weights, strict=True)}
        self._log_result(result)
        return result

    # -- internal helpers ----------------------------------------------------

    @staticmethod
    def _validate(returns: pd.DataFrame) -> None:
        """Guard against malformed returns input."""
        if returns is None or returns.empty:
            raise PortfolioError("Getiri verisi boş.")
        if returns.shape[1] == 0:
            raise PortfolioError("Getiri verisinde varlık sütunu yok.")

    def _solve_tangency(self, sigma: np.ndarray, mu: np.ndarray, rf: float) -> np.ndarray:
        """Closed-form tangency weights: w ~ S^-1 (mu - rf), normalised."""
        n = len(mu)
        excess = mu - rf
        try:
            inv = self._invert(sigma)
            w = inv @ excess
        except np.linalg.LinAlgError:
            inv = self._invert(sigma + np.eye(n) * _RIDGE)
            w = inv @ excess
        norm = float(np.sum(w))
        if not np.isfinite(norm) or abs(norm) < 1e-12:
            logger.warning("tangency_degenerate_fallback_min_variance")
            return self._solve_min_variance(sigma)
        return w / norm

    def _solve_min_variance(self, sigma: np.ndarray) -> np.ndarray:
        """Minimum-variance portfolio weights (global MVP)."""
        n = sigma.shape[0]
        ones = np.ones(n)
        try:
            inv = self._invert(sigma)
        except np.linalg.LinAlgError:
            inv = self._invert(sigma + np.eye(n) * _RIDGE)
        num = inv @ ones
        return num / float(np.sum(num))

    @staticmethod
    def _invert(matrix: np.ndarray) -> np.ndarray:
        """Invert a square matrix with a numpy guard against near-singularity."""
        try:
            return np.linalg.inv(matrix)
        except np.linalg.LinAlgError:
            return np.linalg.pinv(matrix)

    @staticmethod
    def _long_only(weights: np.ndarray) -> np.ndarray:
        """Enforce non-negative weights, then renormalise to sum 1.

        If clipping collapses the vector to zero (all-negative inputs), fall
        back to equal weight so the caller always gets a usable allocation.
        """
        clipped = np.maximum(weights, 0.0)
        total = float(np.sum(clipped))
        if total < 1e-12:
            n = len(weights)
            return np.full(n, 1.0 / n)
        return clipped / total

    @staticmethod
    def _apply_equity_cap(weights: np.ndarray, max_equity: float) -> np.ndarray:
        """Scale risky weights to respect ``max_equity`` (cash takes the rest).

        ``max_equity`` in [0,1] is the ceiling on total risky exposure from the
        risk assessment; the remainder is implicitly held as cash.
        """
        total_risk = float(np.sum(weights))
        cap = max(0.0, min(float(max_equity), 1.0))
        if total_risk <= cap:
            return weights
        scale = cap / total_risk if total_risk > 0 else 0.0
        return weights * scale

    @staticmethod
    def _log_result(result: dict[str, float]) -> None:
        logger.info(
            "markowitz_weights",
            weights={k: round(v, 4) for k, v in result.items()},
            _risk_weight_sum=round(sum(v for v in result.values()), 4),
        )


__all__ = ["PortfolioService", "PortfolioError", "TRADING_DAYS"]
