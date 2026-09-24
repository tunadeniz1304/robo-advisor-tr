"""Tests for the investment policy loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.policy import PolicyError, get_policy, load_policy


def test_policy_loads_and_is_consistent() -> None:
    policy = get_policy()
    assert sorted(policy.model_portfolios) == list(range(1, 11))
    for weights in policy.model_portfolios.values():
        assert sum(weights.values()) == pytest.approx(1.0)
    # Riskli sınıfların ağırlığı seviye ile artar (monotonluk)
    risky = [
        policy.model_weights(lv).get("bist_endeks", 0)
        + policy.model_weights(lv).get("bist_hisse", 0)
        for lv in range(1, 11)
    ]
    assert risky == sorted(risky)
    assert policy.risk_label(1) == "Çok Muhafazakâr" and policy.risk_label(10) == "Çok Agresif"
    assert policy.band_for("bist_hisse") > policy.band_for("para_piyasasi")
    assert policy.withholding_rate("para_piyasasi", 10) >= 0
    assert policy.max_instrument_risk[1] < policy.max_instrument_risk[10]


def test_policy_rejects_bad_weights(tmp_path: Path) -> None:
    src = Path(__file__).resolve().parent.parent / "config" / "policy.toml"
    text = src.read_text(encoding="utf-8").replace(
        "para_piyasasi = 0.70", "para_piyasasi = 0.90", 1
    )
    bad = tmp_path / "policy.toml"
    bad.write_text(text, encoding="utf-8")
    with pytest.raises(PolicyError, match="toplamı"):
        load_policy(bad)
