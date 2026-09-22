"""Unit tests for the deterministic risk-scoring model and Markowitz MPT."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from models import Customer
from services.portfolio_service import PortfolioError, PortfolioService
from services.risk_service import RiskService


def _customer(**overrides: object) -> Customer:
    defaults: dict[str, object] = {
        "id": 1,
        "full_name": "Test",
        "email": "t@example.com",
        "investment_horizon_years": 5,
        "monthly_income": 40000.0,
        "declared_risk_tolerance": 3,
        "financial_goal": None,
    }
    defaults.update(overrides)
    return Customer(**defaults)  # type: ignore[arg-type]


# ----------------------------------------------------------------- risk -----


def test_risk_score_monotonic_in_tolerance() -> None:
    svc = RiskService()
    low = svc.assess(_customer(declared_risk_tolerance=1)).score
    high = svc.assess(_customer(declared_risk_tolerance=5)).score
    assert high > low
    assert 0.0 <= low <= 100.0 and 0.0 <= high <= 100.0


def test_risk_score_increases_with_horizon() -> None:
    svc = RiskService()
    short = svc.assess(_customer(investment_horizon_years=2)).score
    long = svc.assess(_customer(investment_horizon_years=25)).score
    assert long > short


def test_risk_category_bounds() -> None:
    svc = RiskService()
    cons = svc.assess(_customer(declared_risk_tolerance=1, investment_horizon_years=1)).category
    aggr = svc.assess(_customer(declared_risk_tolerance=5, investment_horizon_years=30)).category
    assert cons == "Conservative"
    assert aggr == "Aggressive"
    assert svc.category_bounds(39.9)[1] < svc.category_bounds(80.0)[1]


# ----------------------------------------------------------------- MPT ------


def _returns_frame(n_assets: int = 3, n_days: int = 120) -> pd.DataFrame:
    rng = np.random.default_rng(1)
    # Distinct drift per asset so the optimizer can differentiate them.
    drifts = np.linspace(0.0002, 0.0012, n_assets)
    data = {
        f"A{i}": rng.normal(drift, 0.01, n_days) for i, drift in enumerate(drifts)
    }
    return pd.DataFrame(data)


def test_markowitz_weights_valid() -> None:
    svc = PortfolioService()
    frame = _returns_frame()
    weights = svc.tangency_weights(frame)

    assert set(weights) == set(frame.columns)
    assert all(w >= 0 for w in weights.values())
    assert abs(sum(weights.values()) - 1.0) < 1e-6


def test_markowitz_respects_equity_cap() -> None:
    svc = PortfolioService()
    frame = _returns_frame()
    # Cap at 40%: total risky weight must not exceed the cap (rest is cash).
    weights = svc.tangency_weights(frame, max_equity_weight=0.4)
    assert sum(weights.values()) <= 0.4 + 1e-9


def test_markowitz_single_asset_is_default_equal_weight() -> None:
    """Degenerate single-column input must not crash."""
    svc = PortfolioService()
    frame = pd.DataFrame({"A0": np.random.default_rng(2).normal(0.001, 0.01, 60)})
    weights = svc.tangency_weights(frame)
    assert weights == {"A0": 1.0}


def test_markowitz_empty_input_raises() -> None:
    svc = PortfolioService()
    with pytest.raises(PortfolioError):
        svc.tangency_weights(pd.DataFrame())
