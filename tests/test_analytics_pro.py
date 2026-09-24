"""Tear sheet, TWR/MWR, backtest engine and observability endpoints."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from services.analytics.backtest import compare_policies, run_backtest
from services.analytics.performance import max_drawdown, tear_sheet, time_weighted_return, xirr
from tests.conftest import create_customer, create_portfolio


def test_constant_monthly_return_cagr() -> None:
    idx = pd.date_range("2020-01-31", periods=24, freq="ME")
    sheet = tear_sheet(pd.Series(0.01, index=idx), periods_per_year=12)
    assert sheet["cagr"] == pytest.approx(1.01**12 - 1)
    assert sheet["total_return"] == pytest.approx(1.01**24 - 1)
    assert sheet["max_drawdown"] == 0.0 and sheet["hit_rate"] == 1.0


def test_drawdown_var_and_benchmark() -> None:
    idx = pd.bdate_range("2024-01-01", periods=6)
    r = pd.Series([0.1, -0.2, 0.05, 0.0, 0.1, -0.1], index=idx)
    mdd, _ = max_drawdown(r)
    assert mdd == pytest.approx(-0.2)
    rng = np.random.default_rng(0)
    rr = pd.Series(rng.normal(0.001, 0.01, 500), index=pd.bdate_range("2022-01-03", periods=500))
    sheet = tear_sheet(rr, benchmark=rr)
    assert sheet["benchmark"]["beta"] == pytest.approx(1.0) and sheet["benchmark"][
        "tracking_error"
    ] == pytest.approx(0.0)
    assert sheet["cvar_95_hist"] >= sheet["var_95_hist"] > 0
    assert sheet["cvar_95_param"] >= sheet["var_95_param"]


def test_twr_removes_cash_flow_effect() -> None:
    idx = pd.bdate_range("2024-01-01", periods=3)
    values = pd.Series([100.0, 110.0, 221.0], index=idx)  # gün 3'te +100 yatırım
    flows = pd.Series([0.0, 0.0, 100.0], index=idx)
    twr, daily = time_weighted_return(values, flows)
    assert daily.iloc[0] == pytest.approx(0.10) and daily.iloc[1] == pytest.approx(0.10)
    assert twr == pytest.approx(0.21)


def test_xirr_known_value() -> None:
    assert xirr([(date(2023, 1, 1), -1000.0), (date(2024, 1, 1), 1100.0)]) == pytest.approx(
        0.1, abs=1e-3
    )
    assert xirr([(date(2023, 1, 1), 1000.0)]) is None


def test_backtest_policies_on_synthetic_prices() -> None:
    idx = pd.bdate_range("2020-01-01", periods=504)
    prices = pd.DataFrame(
        {
            "TL_PPF": 100 * 1.0005 ** np.arange(504),
            "THYAO.IS": 100 * np.exp(np.cumsum(np.random.default_rng(1).normal(0.001, 0.03, 504))),
        },
        index=idx,
    )
    target = {"TL_PPF": 0.5, "THYAO.IS": 0.5}
    res = compare_policies(prices, target)
    assert res["none"]["rebalances"] == 0
    assert res["calendar"]["rebalances"] == 503 // 21
    assert res["band"]["rebalances"] >= 1
    assert res["band"]["total_cost"] > res["none"]["total_cost"]
    flat = run_backtest(prices[["TL_PPF"]], {"TL_PPF": 1.0}, policy_name="none")
    assert flat["stats"]["volatility"] == pytest.approx(0.0, abs=1e-9)


def test_report_and_backtest_api(client: TestClient) -> None:
    cid = create_customer(client)["id"]
    pid = create_portfolio(client, cid, cash=300_000.0, holdings={})["id"]
    prop = client.post(f"/api/v1/advisor/rebalance/{pid}", params={"customer_id": cid}).json()
    client.post(f"/api/v1/proposals/{prop['proposal_id']}/approve")
    rep = client.get(f"/api/v1/portfolios/{pid}/report").json()
    assert "twr" in rep and "tear_sheet" in rep and rep["value_curve"]
    bt = client.post("/api/v1/backtest", json={"level": 6, "years": 3})
    assert bt.status_code == 200, bt.text
    assert set(bt.json()["results"]) == {"none", "calendar", "band"}
    assert "benchmark" in bt.json()["results"]["band"]["stats"]


def test_metrics_and_health(client: TestClient) -> None:
    client.get("/api/v1/customers")
    metrics = client.get("/metrics").text
    for name in (
        "advisor_http_requests_total",
        "advisor_optimizer_duration_seconds",
        "advisor_llm_calls_total",
        "advisor_market_data_live",
    ):
        assert name in metrics
    ready = client.get("/health/ready").json()
    assert ready["status"] == "ok" and ready["checks"]["database"] == "ok"
    assert client.get("/health/live").json()["status"] == "ok"


def test_twr_ignores_unfunded_period() -> None:
    """Regression: a near-zero pre-funding value must not explode the TWR."""
    idx = pd.bdate_range("2024-01-01", periods=4)
    values = pd.Series([0.0, 1e-7, 100_000.0, 110_000.0], index=idx)
    flows = pd.Series([0.0, 0.0, 100_000.0, 0.0], index=idx)
    twr, _ = time_weighted_return(values, flows)
    assert twr == pytest.approx(0.10)
