"""v2 audit findings A.7 (true Idzorek Ω) and A.8 (optimiser has a say).

A.7 — Idzorek (2005) defines the view uncertainty ω_k implicitly: the weight
tilt caused by view *k* alone must equal ``c_k`` × the tilt at 100 %
confidence. We solve it per view with a 1-D search and compare with the
closed form PyPortfolioOpt uses for ``omega="idzorek"``.

A.8 — within the class bands, HRP and min-CVaR must produce materially
different allocations in a high-volatility regime.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services.optimization import strategies as st


def _cov() -> np.ndarray:
    vols = np.array([0.05, 0.12, 0.20, 0.30])
    corr = np.array(
        [
            [1.0, 0.3, 0.1, 0.0],
            [0.3, 1.0, 0.4, 0.2],
            [0.1, 0.4, 1.0, 0.6],
            [0.0, 0.2, 0.6, 1.0],
        ]
    )
    return corr * np.outer(vols, vols)


def _implied_weights(mu: np.ndarray, cov: np.ndarray, delta: float) -> np.ndarray:
    return np.linalg.solve(delta * cov, mu)


@pytest.mark.parametrize("confidence", [0.1, 0.35, 0.6, 0.9])
def test_idzorek_omega_hits_target_tilt(confidence: float) -> None:
    cov = _cov()
    w_mkt = np.array([0.4, 0.3, 0.2, 0.1])
    p = np.array([[0.0, 0.0, 1.0, 0.0]])
    q = np.array([0.45])
    tau, delta = 0.05, 2.5
    omega = st.idzorek_omega(cov, w_mkt, p, q, np.array([confidence]), tau=tau, risk_aversion=delta)
    res = st.black_litterman_posterior(
        cov, w_mkt, p, q, np.array([confidence]), tau=tau, risk_aversion=delta, omega=omega
    )
    full = st.black_litterman_posterior(
        cov, w_mkt, p, q, np.array([1.0 - 1e-9]), tau=tau, risk_aversion=delta
    )
    w_c = _implied_weights(res.posterior, cov, delta)
    w_100 = _implied_weights(full.posterior, cov, delta)
    tilt_c, tilt_100 = w_c - w_mkt, w_100 - w_mkt
    ratio = float(tilt_c @ tilt_100 / (tilt_100 @ tilt_100))
    assert ratio == pytest.approx(confidence, abs=1e-4)


def test_idzorek_matches_pypfopt_closed_form_for_multiple_views() -> None:
    cov = _cov()
    w_mkt = np.array([0.4, 0.3, 0.2, 0.1])
    p = np.array([[0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, -1.0]])
    q = np.array([0.30, 0.05])
    conf = np.array([0.7, 0.25])
    tau = 0.05
    ours = st.idzorek_omega(cov, w_mkt, p, q, conf, tau=tau, risk_aversion=2.5)
    # PyPortfolioOpt BlackLittermanModel(omega="idzorek"): ω_k = τ·(1−c)/c · p_k Σ p_kᵀ
    reference = np.diag(
        [tau * (1 - c) / c * float(pk @ cov @ pk) for pk, c in zip(p, conf, strict=True)]
    )
    np.testing.assert_allclose(np.diag(ours), np.diag(reference), rtol=1e-4)
    pypfopt = pytest.importorskip("pypfopt")
    bl = pypfopt.BlackLittermanModel(
        pd.DataFrame(cov),
        pi=np.zeros(4),
        P=p,
        Q=q,
        omega="idzorek",
        view_confidences=conf,
        tau=tau,
    )
    np.testing.assert_allclose(np.diag(ours), np.diag(bl.omega), rtol=1e-4)


def test_black_litterman_payload_names_the_omega_method() -> None:
    import inspect

    from services.optimization.service import OptimizationService

    src = inspect.getsource(OptimizationService._black_litterman)
    assert "idzorek_omega" in src
    assert "Idzorek" in (st.black_litterman_posterior.__doc__ or "")
    assert "Walters" not in (st.black_litterman_posterior.__doc__ or "").split("Idzorek")[0]


def _high_vol_returns(seed: int = 3) -> pd.DataFrame:
    """Three years of daily returns in a stressed, fat-tailed regime."""
    from services.market_data.universe import UNIVERSE

    rng = np.random.default_rng(seed)
    n = 756
    idx = pd.bdate_range("2018-01-01", periods=n)
    market = rng.standard_t(3, n) * 0.02
    fx = rng.standard_t(3, n) * 0.012
    cols = {}
    for spec in UNIVERSE:
        base = {
            "para_piyasasi": 0.0002,
            "tl_tahvil": 0.004,
            "eurobond": 0.008,
            "altin": 0.012,
            "doviz": 0.010,
            "bist_endeks": 0.020,
            "bist_hisse": 0.028,
        }[spec.asset_class]
        noise = rng.standard_t(3, n) * base
        beta = {"bist_endeks": 1.0, "bist_hisse": 1.2}.get(spec.asset_class, 0.0)
        fx_beta = {"doviz": 1.0, "altin": 0.8, "eurobond": 0.9}.get(spec.asset_class, 0.0)
        cols[spec.symbol] = 0.0004 + noise + beta * market + fx_beta * fx
    return pd.DataFrame(cols, index=idx)


def test_hrp_and_min_cvar_differ_in_high_vol_regime() -> None:
    from services.optimization.service import OptimizationRequest, OptimizationService

    svc = OptimizationService(market=None)  # type: ignore[arg-type]
    rets = _high_vol_returns()
    hrp = svc.optimize_on_returns(OptimizationRequest(level=6, method="hrp"), rets, rf=0.40)
    cvar = svc.optimize_on_returns(OptimizationRequest(level=6, method="min_cvar"), rets, rf=0.40)
    keys = set(hrp.weights) | set(cvar.weights)
    l1 = sum(abs(hrp.weights.get(k, 0.0) - cvar.weights.get(k, 0.0)) for k in keys)
    assert l1 >= 0.15, l1
    # Yöntemler sınıf bantlarının içinde kalır ama bantları tam doldurmak zorunda değildir.
    for res in (hrp, cvar):
        for cls, w in res.class_weights.items():
            lo, hi = res.params["class_bounds"][cls]
            assert lo - 1e-6 <= w <= hi + 1e-6
