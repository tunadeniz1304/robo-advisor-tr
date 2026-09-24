"""Unit tests for drift-based rebalancing trigger analysis."""

from __future__ import annotations

import pytest

from services.analytics_service import AnalyticsService
from services.market_service import MarketSnapshot


def _snap(ticker: str, price: float) -> MarketSnapshot:
    return MarketSnapshot(
        ticker=ticker,
        last_price=price,
        momentum_1m=0.0,
        volatility_annualized=0.1,
        daily_returns={},
    )


def test_drift_within_band_no_rebalance() -> None:
    """Small deviations under the trigger band do not require rebalancing.

    Regression for bug #9: the old test asserted ``needs_rebalance is True``
    despite its name; the scenario below really is inside the band.
    """
    result = AnalyticsService().drift_analysis(
        portfolio_id=1,
        cash=200.0,
        holdings={"AAA": 10.0, "BBB": 10.0},
        snapshots={"AAA": _snap("AAA", 40.0), "BBB": _snap("BBB", 40.0)},
        target_weights={"AAA": 0.42, "BBB": 0.38},
        trigger_band=0.05,
    )
    assert result["needs_rebalance"] is False, result
    assert result["max_drift"] == pytest.approx(0.02)
    assert result["cash_weight"] == pytest.approx(0.2)
    assert all(not row["outside_band"] for row in result["assets"])


def test_drift_just_outside_band_triggers() -> None:
    """A 10pp deviation with a 5pp band triggers rebalancing."""
    result = AnalyticsService().drift_analysis(
        portfolio_id=1,
        cash=200.0,
        holdings={"AAA": 10.0, "BBB": 10.0},
        snapshots={"AAA": _snap("AAA", 40.0), "BBB": _snap("BBB", 40.0)},
        target_weights={"AAA": 0.5, "BBB": 0.4},
        trigger_band=0.05,
    )
    assert result["needs_rebalance"] is True
    assert result["max_drift"] == pytest.approx(0.1)


def test_drift_outside_band_triggers_rebalance() -> None:
    """A large deviation past the band flags that rebalancing is needed."""
    result = AnalyticsService().drift_analysis(
        portfolio_id=1,
        cash=0.0,
        holdings={"AAA": 90.0},
        snapshots={"AAA": _snap("AAA", 10.0)},
        target_weights={"AAA": 0.4},
        trigger_band=0.05,
    )
    assert result["needs_rebalance"] is True
    assert result["max_drift"] == pytest.approx(0.6)
    assert result["assets"][0]["outside_band"] is True
    assert result["assets"][0]["actual_weight"] == pytest.approx(1.0)
