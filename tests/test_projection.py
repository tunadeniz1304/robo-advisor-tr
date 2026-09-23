"""Unit tests for the Monte Carlo wealth projection."""
from __future__ import annotations

import numpy as np
import pytest

from services.analytics_service import MonteCarloProjection


def test_projection_median_grows_with_positive_return() -> None:
    proj = MonteCarloProjection(
        current_value=100000.0,
        annual_return=0.08,
        annual_vol=0.15,
        horizon_years=10.0,
        n_simulations=5000,
    )
    res = proj.run(rng=np.random.default_rng(7))
    assert res["p50"] > 100000.0
    assert 0.0 < res["p5"] < res["p95"]


def test_projection_neutral_when_no_value() -> None:
    res = MonteCarloProjection(0.0, 0.08, 0.15, 10.0).run()
    assert res["p50"] == 0.0
    assert res["simulations"] == 0


def test_projection_guarantees_ordering() -> None:
    res = MonteCarloProjection(50000.0, 0.04, 0.2, 20.0, n_simulations=3000).run(rng=np.random.default_rng(3))
    assert res["p5"] <= res["p50"] <= res["p95"]
