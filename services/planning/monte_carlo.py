"""Goal Monte Carlo: moving-block bootstrap of historical monthly returns.

Inputs are the portfolio's **nominal TL** monthly returns and monthly TÜFE
inflation, sampled *jointly* (same month indices) in blocks of ``L`` months
so fat tails, crises, FX shocks and the inflation link are preserved —
unlike a Gaussian GBM.

Wealth recursion in real terms (contributions at the start of each month)::

    W_{t+1} = (W_t + c) · (1 + r_t) / (1 + π_t)

For a fixed set of bootstrap paths the final wealth is **linear in c**::

    W_T = A + c · B,   A = W_0 Π_t g_t,   B = Σ_t Π_{s≥t} g_s,   g = (1+r)/(1+π)

so the success probability is ``mean(A + cB ≥ K)`` and the monthly
contribution needed for a success probability ``p`` is exactly the
``p``-quantile of ``c_i* = max(0, (K − A_i) / B_i)`` — no bisection noise.
With zero volatility the result equals the closed-form future value.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from core.metrics import MONTE_CARLO_SECONDS


@dataclass(frozen=True)
class SimulationSpec:
    """One goal simulation request (real TL amounts)."""

    initial: float
    monthly_contribution: float
    target_real: float
    months: int
    n_paths: int = 10_000
    block_months: int = 6
    seed: int = 42
    success_threshold: float = 0.80


@dataclass
class SimulationResult:
    success_probability: float
    required_monthly_contribution: float
    p10_real: float
    p50_real: float
    p90_real: float
    p50_nominal: float
    bands: list[dict[str, float]] = field(default_factory=list)
    shortfall_median: float = 0.0
    elapsed_ms: float = 0.0
    spec: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success_probability": round(self.success_probability, 4),
            "required_monthly_contribution": round(self.required_monthly_contribution, 2),
            "p10_real": round(self.p10_real, 2),
            "p50_real": round(self.p50_real, 2),
            "p90_real": round(self.p90_real, 2),
            "p50_nominal": round(self.p50_nominal, 2),
            "shortfall_median": round(self.shortfall_median, 2),
            "bands": self.bands,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "spec": self.spec,
        }


def bootstrap_indices(
    n_obs: int, months: int, n_paths: int, block: int, rng: np.random.Generator
) -> np.ndarray:
    """Moving-block bootstrap month indices of shape ``(n_paths, months)``."""
    block = max(1, min(block, n_obs))
    n_blocks = -(-months // block)
    starts = rng.integers(0, n_obs - block + 1, size=(n_paths, n_blocks))
    idx = starts[:, :, None] + np.arange(block)[None, None, :]
    return idx.reshape(n_paths, n_blocks * block)[:, :months]


def _paths(history: np.ndarray, spec: SimulationSpec) -> tuple[np.ndarray, np.ndarray]:
    """Real growth factors ``g`` and inflation factors per path/month."""
    rng = np.random.default_rng(spec.seed)
    idx = bootstrap_indices(history.shape[0], spec.months, spec.n_paths, spec.block_months, rng)
    nominal = history[idx, 0]
    infl = history[idx, 1]
    growth = (1.0 + nominal) / (1.0 + infl)
    return growth, 1.0 + infl


def linear_terms(growth: np.ndarray, initial: float) -> tuple[np.ndarray, np.ndarray]:
    """``A`` and ``B`` of ``W_T = A + c·B`` for each path."""
    # tail[:, t] = Π_{s≥t} g_s
    tail = np.cumprod(growth[:, ::-1], axis=1)[:, ::-1]
    a = initial * tail[:, 0]
    b = tail.sum(axis=1)
    return a, b


def required_contribution(a: np.ndarray, b: np.ndarray, target: float, probability: float) -> float:
    """Smallest monthly contribution reaching ``target`` with ``probability``."""
    needed = np.maximum((target - a) / np.maximum(b, 1e-12), 0.0)
    return float(np.quantile(needed, probability))


def simulate(history: np.ndarray, spec: SimulationSpec) -> SimulationResult:
    """Run the goal simulation.

    Args:
        history: ``(T, 2)`` array of monthly ``[portfolio_nominal_return,
            inflation]`` observations (TL).
        spec: Simulation parameters.

    Returns:
        :class:`SimulationResult` with success probability, required monthly
        contribution, final percentiles and yearly real/nominal bands.
    """
    started = time.perf_counter()
    if spec.months <= 0:
        raise ValueError("Vade en az 1 ay olmalı.")
    growth, infl = _paths(history, spec)
    a, b = linear_terms(growth, spec.initial)
    c = spec.monthly_contribution
    final_real = a + c * b
    success = float(np.mean(final_real >= spec.target_real - 1e-9))
    required = required_contribution(a, b, spec.target_real, spec.success_threshold)

    # Yıllık bantlar: servet yolu (reel ve nominal)
    wealth = np.full(growth.shape[0], float(spec.initial))
    price_level = np.ones(growth.shape[0])
    bands: list[dict[str, float]] = [
        {
            "month": 0,
            "p10": spec.initial,
            "p50": spec.initial,
            "p90": spec.initial,
            "p50_nominal": spec.initial,
        }
    ]
    for t in range(spec.months):
        wealth = (wealth + c) * growth[:, t]
        price_level = price_level * infl[:, t]
        if (t + 1) % 12 == 0 or t + 1 == spec.months:
            p10, p50, p90 = np.percentile(wealth, [10, 50, 90])
            bands.append(
                {
                    "month": t + 1,
                    "p10": round(float(p10), 2),
                    "p50": round(float(p50), 2),
                    "p90": round(float(p90), 2),
                    "p50_nominal": round(float(np.percentile(wealth * price_level, 50)), 2),
                }
            )
    p10, p50, p90 = np.percentile(final_real, [10, 50, 90])
    elapsed = (time.perf_counter() - started) * 1000.0
    MONTE_CARLO_SECONDS.observe(elapsed / 1000.0)
    return SimulationResult(
        success_probability=success,
        required_monthly_contribution=required,
        p10_real=float(p10),
        p50_real=float(p50),
        p90_real=float(p90),
        p50_nominal=float(np.percentile(final_real * np.prod(infl, axis=1), 50)),
        bands=bands,
        shortfall_median=float(max(spec.target_real - p50, 0.0)),
        elapsed_ms=elapsed,
        spec={
            "initial": spec.initial,
            "monthly_contribution": spec.monthly_contribution,
            "target_real": spec.target_real,
            "months": spec.months,
            "n_paths": spec.n_paths,
            "block_months": spec.block_months,
            "seed": spec.seed,
            "success_threshold": spec.success_threshold,
            "history_months": int(history.shape[0]),
        },
    )


__all__ = [
    "SimulationResult",
    "SimulationSpec",
    "bootstrap_indices",
    "linear_terms",
    "required_contribution",
    "simulate",
]
