"""Goal planning: bootstrap Monte Carlo correctness, performance and API."""

from __future__ import annotations

import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from services.planning.monte_carlo import (
    SimulationSpec,
    bootstrap_indices,
    linear_terms,
    simulate,
)
from tests.conftest import create_customer


def _const_history(r: float, infl: float, months: int = 120) -> np.ndarray:
    return np.column_stack([np.full(months, r), np.full(months, infl)])


def test_zero_volatility_matches_closed_form() -> None:
    r, infl, n, c, w0 = 0.02, 0.01, 60, 1000.0, 50_000.0
    g = (1 + r) / (1 + infl)
    closed = w0 * g**n + c * sum(g**k for k in range(1, n + 1))
    res = simulate(
        _const_history(r, infl),
        SimulationSpec(
            initial=w0, monthly_contribution=c, target_real=closed * 0.999, months=n, n_paths=200
        ),
    )
    assert res.p50_real == pytest.approx(closed, rel=1e-9)
    assert res.p10_real == pytest.approx(res.p90_real)
    assert res.success_probability == 1.0
    # Gerekli katkı: tam hedefe ulaştıran c
    target = closed
    need = (target - w0 * g**n) / sum(g**k for k in range(1, n + 1))
    res2 = simulate(
        _const_history(r, infl),
        SimulationSpec(
            initial=w0, monthly_contribution=0, target_real=target, months=n, n_paths=100
        ),
    )
    assert res2.required_monthly_contribution == pytest.approx(need, rel=1e-9)


def test_simulation_is_deterministic_with_seed() -> None:
    rng = np.random.default_rng(0)
    hist = np.column_stack([rng.normal(0.02, 0.06, 120), rng.normal(0.02, 0.005, 120)])
    spec = SimulationSpec(initial=10_000, monthly_contribution=500, target_real=60_000, months=96)
    assert (
        simulate(hist, spec).to_dict()["success_probability"]
        == simulate(hist, spec).to_dict()["success_probability"]
    )


def test_required_contribution_hits_threshold() -> None:
    rng = np.random.default_rng(1)
    hist = np.column_stack([rng.normal(0.025, 0.07, 120), rng.normal(0.02, 0.004, 120)])
    spec = SimulationSpec(
        initial=10_000,
        monthly_contribution=0,
        target_real=200_000,
        months=120,
        success_threshold=0.8,
    )
    need = simulate(hist, spec).required_monthly_contribution
    check = simulate(
        hist, SimulationSpec(**{**spec.__dict__, "monthly_contribution": need * 1.0001})
    )
    assert check.success_probability >= 0.8 - 1e-9
    lower = simulate(hist, SimulationSpec(**{**spec.__dict__, "monthly_contribution": need * 0.95}))
    assert lower.success_probability < 0.8


def test_linear_terms_match_recursion() -> None:
    rng = np.random.default_rng(2)
    g = 1 + rng.normal(0.01, 0.03, size=(5, 24))
    a, b = linear_terms(g, 1000.0)
    w = np.full(5, 1000.0)
    for t in range(24):
        w = (w + 300.0) * g[:, t]
    np.testing.assert_allclose(a + 300.0 * b, w)


def test_block_bootstrap_keeps_blocks_contiguous() -> None:
    idx = bootstrap_indices(100, 24, 50, 6, np.random.default_rng(3))
    assert idx.shape == (50, 24) and idx.max() < 100
    blocks = idx.reshape(50, 4, 6)
    assert np.all(np.diff(blocks, axis=2) == 1)


def test_ten_thousand_paths_under_one_second() -> None:
    rng = np.random.default_rng(4)
    hist = np.column_stack([rng.normal(0.02, 0.05, 120), rng.normal(0.02, 0.005, 120)])
    started = time.perf_counter()
    simulate(
        hist,
        SimulationSpec(
            initial=100_000, monthly_contribution=5_000, target_real=2e6, months=360, n_paths=10_000
        ),
    )
    assert time.perf_counter() - started < 1.0


def test_goal_api_simulate_and_what_if(client: TestClient) -> None:
    cid = create_customer(client)["id"]
    created = client.post(
        f"/api/v1/customers/{cid}/goals",
        json={
            "goal_type": "emeklilik",
            "name": "Emeklilik",
            "target_amount_real": 3_000_000,
            "horizon_years": 15,
            "initial_amount": 200_000,
            "monthly_contribution": 10_000,
        },
    )
    assert created.status_code == 201, created.text
    gid = created.json()["id"]
    sim = client.post(f"/api/v1/goals/{gid}/simulate", params={"with_report": True})
    assert sim.status_code == 200, sim.text
    body = sim.json()
    res = body["result"]
    assert 0.0 <= res["success_probability"] <= 1.0
    assert res["p10_real"] <= res["p50_real"] <= res["p90_real"]
    assert res["p50_nominal"] > res["p50_real"]  # enflasyon
    assert len(res["bands"]) == 16 and res["spec"]["n_paths"] == 10_000
    codes = {w["kod"] for w in res["what_if"]}
    assert {"vade_2_yil_uzat", "katki_yuzde_20_artir"} <= codes
    longer = next(w for w in res["what_if"] if w["kod"] == "vade_2_yil_uzat")
    assert longer["basari_olasiligi"] >= res["success_probability"]
    assert body["report"]["ozet"] and body["report"]["llm_mode"] == "demo"
    assert (
        client.get(f"/api/v1/goals/{gid}").json()["last_simulation"]["success_probability"]
        == res["success_probability"]
    )

    wi = client.post(f"/api/v1/goals/{gid}/what-if", json={"contribution_multiplier": 2.0}).json()
    assert wi["scenario"]["success_probability"] >= wi["base"]["success_probability"]
    assert wi["delta_success_probability"] >= 0

    assert len(client.get(f"/api/v1/customers/{cid}/goals").json()) == 1
    assert (
        client.put(f"/api/v1/goals/{gid}", json={"horizon_years": 20}).json()["horizon_years"] == 20
    )
    assert client.delete(f"/api/v1/goals/{gid}").status_code == 204
