"""Unit tests for drift-based rebalancing trigger analysis.

v2: the legacy ``AnalyticsService.drift_analysis`` (one absolute band for every
position) was removed; the live engine :func:`drift_report` with relative
bands is the only drift implementation, so these scenarios now run on it.
"""

from __future__ import annotations

import pytest

from services.rebalancing.engine import PortfolioState, drift_report

PRICES = {"AAA": 2.0, "BBB": 2.0}


def test_drift_within_band_no_rebalance() -> None:
    """Small deviations under the band do not require rebalancing.

    Regression for bug #9: the old test asserted ``needs_rebalance is True``
    despite its name; the scenario below really is inside the band.
    """
    state = PortfolioState(cash=0.0, quantities={"AAA": 21.0, "BBB": 29.0}, prices=PRICES)
    result = drift_report(state, {"AAA": 0.40, "BBB": 0.60})
    assert result["needs_rebalance"] is False, result
    assert result["max_drift"] == pytest.approx(0.02)
    assert all(not row["outside_band"] for row in result["assets"])
    assert {row["band"] for row in result["assets"]} == {0.05}  # min(%5, %25 × hedef)


def test_drift_just_outside_band_triggers() -> None:
    """A 10pp deviation on a 40 % target (band 5pp) triggers rebalancing."""
    state = PortfolioState(cash=0.0, quantities={"AAA": 25.0, "BBB": 25.0}, prices=PRICES)
    result = drift_report(state, {"AAA": 0.40, "BBB": 0.60})
    assert result["needs_rebalance"] is True
    assert result["max_drift"] == pytest.approx(0.1)


def test_drift_outside_band_triggers_rebalance() -> None:
    """A large deviation past the band flags that rebalancing is needed."""
    state = PortfolioState(cash=0.0, quantities={"AAA": 90.0}, prices={"AAA": 10.0})
    result = drift_report(state, {"AAA": 0.4})
    assert result["needs_rebalance"] is True
    assert result["max_drift"] == pytest.approx(0.6)
    assert result["assets"][0]["outside_band"] is True
    assert result["assets"][0]["actual_weight"] == pytest.approx(1.0)


def test_idle_cash_triggers_rebalance() -> None:
    state = PortfolioState(cash=200.0, quantities={"AAA": 400.0}, prices={"AAA": 2.0})
    result = drift_report(state, {"AAA": 1.0})
    assert result["idle_cash"] is True and result["needs_rebalance"] is True
