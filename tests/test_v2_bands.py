"""v2 audit finding A.5: relative + absolute drift bands (Betterment style).

``band_i = min(abs_cap, max(abs_floor, rel · target_i))`` per instrument and
an absolute band per asset class. The ASELS scenario (target 1.3 %, actual
7.6 % — 5.8× the target) was reported "inside the band" by the old absolute
per-class band of 7 pp.
"""

from __future__ import annotations

import pytest

from core.policy import get_policy
from services.rebalancing.engine import PortfolioState, drift_report, plan_trades


def _asels_state() -> tuple[PortfolioState, dict[str, float]]:
    prices = {"ASELS.IS": 1.0, "XU100.IS": 1.0, "TL_PPF": 1.0}
    quantities = {"ASELS.IS": 7_600.0, "XU100.IS": 50_000.0, "TL_PPF": 42_400.0}
    target = {"ASELS.IS": 0.013, "XU100.IS": 0.55, "TL_PPF": 0.437}
    return PortfolioState(cash=0.0, quantities=quantities, prices=prices), target


def test_asels_scenario_triggers_rebalance() -> None:
    state, target = _asels_state()
    report = drift_report(state, target)
    asels = next(r for r in report["assets"] if r["symbol"] == "ASELS.IS")
    assert asels["actual_weight"] == pytest.approx(0.076)
    assert asels["outside_band"] is True
    assert report["needs_rebalance"] is True


def test_asels_scenario_plan_sells_asels() -> None:
    state, target = _asels_state()
    plan = plan_trades(state, target)
    assert plan.needs_rebalance
    assert plan.trades.get("ASELS.IS", 0.0) < 0
    assert plan.after_weights["ASELS.IS"] <= 0.013 + 0.005 + 1e-6


def test_band_formula_relative_with_floor_and_cap() -> None:
    from services.rebalancing.engine import drift_band

    policy = get_policy()
    cfg = policy.rebalance["drift_band"]
    assert cfg == {"abs_floor": 0.005, "rel": 0.25, "abs_cap": 0.05, "class_abs": 0.03}
    assert drift_band(0.013, policy) == pytest.approx(0.005)  # taban
    assert drift_band(0.10, policy) == pytest.approx(0.025)  # göreli
    assert drift_band(0.40, policy) == pytest.approx(0.05)  # tavan
    assert drift_band(0.0, policy) == 0.0  # hedefte olmayan pozisyon: bant yok


def test_class_level_absolute_band() -> None:
    # Enstrümanlar kendi bantlarında, ama sınıf toplamı %3'ten fazla kaymış.
    prices = {"AKBNK.IS": 1.0, "GARAN.IS": 1.0, "YKBNK.IS": 1.0, "TL_PPF": 1.0}
    target = {"AKBNK.IS": 0.20, "GARAN.IS": 0.20, "YKBNK.IS": 0.20, "TL_PPF": 0.40}
    quantities = {
        "AKBNK.IS": 21_300.0,
        "GARAN.IS": 21_300.0,
        "YKBNK.IS": 21_300.0,
        "TL_PPF": 36_100.0,
    }
    report = drift_report(PortfolioState(0.0, quantities, prices), target)
    assert not any(r["outside_band"] for r in report["assets"])
    bist = next(c for c in report["classes"] if c["asset_class"] == "bist_hisse")
    assert bist["band"] == pytest.approx(0.03)
    assert bist["outside_band"] is True
    assert report["needs_rebalance"] is True
