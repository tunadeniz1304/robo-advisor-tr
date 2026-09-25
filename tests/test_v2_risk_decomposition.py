"""v2 §2.26: risk decomposition by asset class and factor analysis."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from services.analytics.risk_decomposition import class_risk_contributions, factor_decomposition
from tests.conftest import create_customer, create_portfolio


def test_euler_contributions_sum_to_volatility_and_group_by_class() -> None:
    syms = ["TL_PPF", "XU100.IS", "THYAO.IS"]
    vols = np.array([0.02, 0.25, 0.40])
    corr = np.array([[1, 0, 0], [0, 1, 0.8], [0, 0.8, 1]])
    cov = pd.DataFrame(corr * np.outer(vols, vols), index=syms, columns=syms)
    w = pd.Series([0.6, 0.3, 0.1], index=syms)
    out = class_risk_contributions(w, cov)
    expected_vol = float(np.sqrt(w.to_numpy() @ cov.to_numpy() @ w.to_numpy()))
    assert out["volatility"] == pytest.approx(expected_vol)
    assert sum(v["risk_share"] for v in out["classes"].values()) == pytest.approx(1.0)
    ppf = out["classes"]["para_piyasasi"]
    assert ppf["weight"] == pytest.approx(0.6) and ppf["risk_share"] < 0.02  # %60 ağırlık, ~%1 risk
    assert list(out["classes"])[0] == "bist_endeks"  # en çok risk taşıyan önce


def test_factor_regression_recovers_known_betas() -> None:
    rng = np.random.default_rng(4)
    idx = pd.date_range("2020-01-03", periods=260, freq="W-FRI")
    f = pd.DataFrame(rng.normal(0, 0.03, (260, 3)), index=idx, columns=["bist", "usdtry", "faiz"])
    r = 0.6 * f["bist"] + 0.3 * f["usdtry"] - 0.2 * f["faiz"] + rng.normal(0, 0.005, 260)
    out = factor_decomposition(r, f)
    assert out["betas"]["bist"]["beta"] == pytest.approx(0.6, abs=0.03)
    assert out["betas"]["usdtry"]["beta"] == pytest.approx(0.3, abs=0.03)
    assert out["betas"]["faiz"]["beta"] == pytest.approx(-0.2, abs=0.03)
    shares = sum(b["variance_share"] for b in out["betas"].values())
    assert shares == pytest.approx(out["r_squared"])
    assert out["r_squared"] + out["idiosyncratic_share"] == pytest.approx(1.0)
    assert out["r_squared"] > 0.9


def test_factor_regression_needs_data() -> None:
    idx = pd.date_range("2020-01-03", periods=5, freq="W-FRI")
    with pytest.raises(ValueError):
        factor_decomposition(pd.Series(0.0, index=idx), pd.DataFrame({"bist": 0.0}, index=idx))


def test_risk_decomposition_endpoint(settings) -> None:  # noqa: ANN001
    from core.app import create_app
    from tests.conftest import FakeMarketSource, auth_headers, login

    app = create_app(settings=settings, market_source=FakeMarketSource(n_days=800))
    with TestClient(app) as client:
        client.headers.update(auth_headers(login(client)))
        cid = create_customer(client, email="risk@example.com")["id"]
        pid = create_portfolio(
            client, cid, cash=10_000.0, holdings={"THYAO.IS": 100.0, "TL_PPF": 500.0}
        )["id"]
        body = client.get(f"/api/v1/portfolios/{pid}/risk-sources").json()
        empty = create_portfolio(client, cid, cash=1_000.0, holdings={})["id"]
        assert client.get(f"/api/v1/portfolios/{empty}/risk-sources").status_code == 409
    assert sum(v["risk_share"] for v in body["classes"].values()) == pytest.approx(1.0)
    assert set(body["factors"]["betas"]) == {"bist", "usdtry", "faiz"}
    assert 0 <= body["factors"]["r_squared"] <= 1
