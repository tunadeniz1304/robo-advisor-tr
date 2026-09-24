"""Optimisation: estimators, strategies (analytic cases) and the service."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.policy import get_policy
from services.market_data.service import MarketDataService
from services.market_data.sources import ChainedSource, SnapshotSource
from services.market_data.universe import BY_SYMBOL
from services.optimization import strategies as st
from services.optimization.estimators import capm_prior, expected_returns, ledoit_wolf
from services.optimization.service import (
    METHODS,
    OptimizationRequest,
    OptimizationService,
    View,
    apply_tilt,
)


@pytest.fixture(scope="module")
def service() -> OptimizationService:
    return OptimizationService(
        MarketDataService(ChainedSource(None, SnapshotSource(), mode="snapshot"))
    )


# ------------------------------------------------------------------ estimators


def test_ledoit_wolf_is_psd_and_shrinks() -> None:
    rng = np.random.default_rng(0)
    rets = pd.DataFrame(rng.normal(0, 0.01, size=(60, 10)))
    cov, delta = ledoit_wolf(rets)
    assert 0.0 <= delta <= 1.0
    assert np.all(np.linalg.eigvalsh(cov) > 0)
    assert np.allclose(cov, cov.T)


def test_expected_returns_shrink_toward_mean() -> None:
    rng = np.random.default_rng(1)
    rets = pd.DataFrame(rng.normal([0.001, 0.0, -0.001], 0.02, size=(250, 3)))
    sample = rets.mean().to_numpy() * 252
    shrunk = expected_returns(rets)
    assert np.ptp(shrunk) <= np.ptp(sample) + 1e-12


def test_capm_prior_beta_one_equals_market() -> None:
    rng = np.random.default_rng(2)
    mkt = pd.Series(rng.normal(0.001, 0.01, 300))
    rets = pd.DataFrame({"A": mkt.to_numpy()})
    prior = capm_prior(rets, mkt, rf=0.1)
    assert prior[0] == pytest.approx(mkt.mean() * 252, rel=1e-6)
    blended = expected_returns(rets, method="capm", rf=0.1, market=mkt)
    assert blended[0] == pytest.approx(mkt.mean() * 252, rel=1e-6)


# ------------------------------------------------------------------ strategies


def test_hrp_two_uncorrelated_assets_is_inverse_variance() -> None:
    cov = np.diag([1.0, 4.0])
    assert st.hrp_weights(cov) == pytest.approx([0.8, 0.2])


def test_risk_parity_diagonal_inverse_vol() -> None:
    cov = np.diag([0.04, 0.16])
    w = st.risk_parity(cov, st.unconstrained(2))
    assert w == pytest.approx([2 / 3, 1 / 3], abs=1e-3)
    rc = st.risk_contributions(w, cov)
    assert rc[0] == pytest.approx(rc[1], abs=1e-3)


def test_min_cvar_prefers_lossless_asset() -> None:
    rng = np.random.default_rng(3)
    scen = np.column_stack([np.abs(rng.normal(0.001, 0.001, 200)), rng.normal(0.0, 0.03, 200)])
    w = st.min_cvar(scen, st.unconstrained(2))
    assert w[0] == pytest.approx(1.0, abs=1e-6)


def test_mean_variance_min_variance_when_below_rf() -> None:
    cov = np.diag([0.01, 0.04])
    w = st.mean_variance(np.array([0.1, 0.2]), cov, rf=0.5, cons=st.unconstrained(2))
    assert w == pytest.approx([0.8, 0.2], abs=1e-4)


def test_black_litterman_confidence_extremes() -> None:
    cov = np.array([[0.04, 0.01], [0.01, 0.09]])
    w_mkt = np.array([0.5, 0.5])
    p = np.array([[1.0, 0.0]])
    q = np.array([0.50])
    lo = st.black_litterman_posterior(cov, w_mkt, p, q, np.array([0.001]))
    hi = st.black_litterman_posterior(cov, w_mkt, p, q, np.array([0.999]))
    mid = st.black_litterman_posterior(cov, w_mkt, p, q, np.array([0.5]))
    assert lo.posterior[0] == pytest.approx(lo.prior[0], abs=1e-3)
    assert hi.posterior[0] == pytest.approx(0.50, abs=1e-3)
    assert lo.posterior[0] < mid.posterior[0] < hi.posterior[0]


def test_projection_respects_group_bounds() -> None:
    cons = st.Constraints(
        lower=np.zeros(3),
        upper=np.ones(3),
        groups={"g": [0, 1]},
        group_lower={"g": 0.6},
        group_upper={"g": 0.7},
    )
    w = st.project(np.array([0.1, 0.1, 0.8]), cons)
    assert cons.violation(w) < 1e-6
    assert w[0] + w[1] == pytest.approx(0.6, abs=1e-5)


def test_infeasible_constraints_raise() -> None:
    cons = st.Constraints(lower=np.zeros(2), upper=np.array([0.2, 0.2]))
    with pytest.raises(st.OptimizationError):
        cons.feasible_start()


def test_apply_tilt_moves_weight_and_preserves_total() -> None:
    w = get_policy().model_weights(6)
    up = apply_tilt(w, 0.05)
    down = apply_tilt(w, -0.05)
    assert sum(up.values()) == pytest.approx(1.0)
    assert up["bist_endeks"] + up["bist_hisse"] == pytest.approx(
        w["bist_endeks"] + w["bist_hisse"] + 0.05
    )
    assert down["para_piyasasi"] > w["para_piyasasi"]


# ------------------------------------------------------------------ service


def _check_constraints(result, level: int) -> None:  # noqa: ANN001
    policy = get_policy()
    band = policy.optimization["class_band"]
    weights = result.weights
    assert sum(weights.values()) == pytest.approx(1.0, abs=1e-6)
    assert all(w >= -1e-9 for w in weights.values())
    for cls, w in result.class_weights.items():
        target = result.model_class_weights.get(cls, 0.0)
        assert target - band - 1e-5 <= w <= target + band + 1e-5, (cls, w, target)
    for sym, w in weights.items():
        if BY_SYMBOL[sym].risk_score >= 5:
            assert w <= policy.optimization["max_single_instrument"] + 1e-5
    assert result.gate.allowed


@pytest.mark.parametrize("method", list(METHODS))
@pytest.mark.parametrize("level", [1, 5, 10])
async def test_every_method_satisfies_constraints(
    service: OptimizationService, method: str, level: int
) -> None:
    views = [View("ALTIN_TL", 0.45, 0.6)] if method == "black_litterman" else []
    result = await service.optimize(OptimizationRequest(level=level, method=method, views=views))
    _check_constraints(result, level)
    assert result.volatility > 0
    assert sum(result.risk_contributions.values()) == pytest.approx(1.0, abs=1e-6)
    assert result.params["history_years"] >= 3


async def test_optimizer_is_deterministic(service: OptimizationService) -> None:
    a = await service.optimize(OptimizationRequest(level=6, method="min_cvar"))
    b = await service.optimize(OptimizationRequest(level=6, method="min_cvar"))
    assert a.weights == b.weights


async def test_low_level_universe_excludes_equities(service: OptimizationService) -> None:
    res = await service.optimize(OptimizationRequest(level=2, method="hrp"))
    assert all(BY_SYMBOL[s].risk_score <= 4 for s in res.weights)
    assert "THYAO.IS" not in res.universe


async def test_exclusions_and_esg_filter(service: OptimizationService) -> None:
    res = await service.optimize(
        OptimizationRequest(level=9, method="hrp", exclude=["THYAO.IS"], esg_only=True)
    )
    assert (
        "THYAO.IS" not in res.weights
        and "BIMAS.IS" not in res.universe
        and "PGSUS.IS" not in res.universe
    )
    assert {"THYAO.IS", "BIMAS.IS", "PGSUS.IS"} <= set(res.excluded)


async def test_black_litterman_view_raises_weight(service: OptimizationService) -> None:
    base = await service.optimize(OptimizationRequest(level=6, method="black_litterman"))
    bull = await service.optimize(
        OptimizationRequest(level=6, method="black_litterman", views=[View("ALTIN_TL", 0.9, 0.8)])
    )
    assert bull.bl is not None
    assert bull.bl["posterior_returns"]["ALTIN_TL"] > bull.bl["prior_returns"]["ALTIN_TL"]
    assert bull.weights.get("ALTIN_TL", 0) >= base.weights.get("ALTIN_TL", 0)


async def test_unknown_method_rejected(service: OptimizationService) -> None:
    with pytest.raises(st.OptimizationError):
        await service.optimize(OptimizationRequest(level=5, method="sihir"))


async def test_model_portfolio_risk_increases_with_level(service: OptimizationService) -> None:
    low = await service.model_portfolio_stats(1)
    high = await service.model_portfolio_stats(10)
    assert high["volatility"] > low["volatility"] > 0


async def test_regime_tilt_shifts_equity(service: OptimizationService) -> None:
    neutral = await service.optimize(OptimizationRequest(level=6, method="risk_parity"))
    risk_on = await service.optimize(
        OptimizationRequest(level=6, method="risk_parity", regime_tilt=0.05)
    )
    eq = lambda r: r.class_weights.get("bist_endeks", 0) + r.class_weights.get("bist_hisse", 0)  # noqa: E731
    assert eq(risk_on) >= eq(neutral) - 1e-6
