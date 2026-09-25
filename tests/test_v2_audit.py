"""v2 bağımsız denetim turu 1 bulgularının regresyon testleri."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.market_data.universe import CLASS_BASKET, model_symbol_weights


def test_equity_class_is_a_basket_not_one_stock() -> None:
    weights = model_symbol_weights({"bist_hisse": 0.30, "doviz": 0.10, "para_piyasasi": 0.60})
    stocks = CLASS_BASKET["bist_hisse"]
    assert len(stocks) >= 10 and "THYAO.IS" in stocks
    assert weights["THYAO.IS"] == pytest.approx(0.30 / len(stocks))
    assert weights["USDTRY"] == weights["EURTRY"] == pytest.approx(0.05)
    assert sum(weights.values()) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "weights",
    [{"XU100.IS": 2.0, "TL_PPF": -1.0}, {"XU100.IS": 0.5}, {}],
)
def test_backtest_rejects_leverage_shorts_and_partial_weights(
    client: TestClient, weights: dict[str, float]
) -> None:
    assert client.post("/api/v1/backtest", json={"weights": weights}).status_code == 422


def test_llm_status_shows_customers_only_the_mode(client: TestClient) -> None:
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "username": "yalniz.mod",
            "password": "Parola!2345",
            "full_name": "Mod Test",
            "email": "mod@example.com",
        },
    ).json()
    body = client.get(
        "/api/v1/llm/status", headers={"Authorization": f"Bearer {reg['access_token']}"}
    ).json()
    assert set(body) == {"mode"}


def test_aggressive_profile_stays_aggressive_under_stress_tilt() -> None:
    from core.policy import get_policy
    from services.optimization.service import OptimizationRequest, OptimizationService
    from tests.test_v2_optimizer import _high_vol_returns

    policy = get_policy()
    model = policy.model_weights(10)
    band = float(policy.optimization["class_band"])
    res = OptimizationService(market=None).optimize_on_returns(  # type: ignore[arg-type]
        OptimizationRequest(level=10, method="hrp", regime_tilt=-0.05),
        _high_vol_returns(),
        rf=0.40,
    )
    risky = res.class_weights.get("bist_endeks", 0.0) + res.class_weights.get("bist_hisse", 0.0)
    assert band <= 0.05
    assert risky >= model["bist_endeks"] + model["bist_hisse"] - 2 * band - 0.05 - 1e-6
    for cls, w in res.class_weights.items():
        assert abs(w - model.get(cls, 0.0)) <= band + 0.05 + 1e-6, cls


def test_old_extreme_moves_are_info_not_warning() -> None:
    import numpy as np
    import pandas as pd

    from services.market_data.quality import assess_series

    idx = pd.bdate_range("2018-01-01", periods=600)
    values = pd.Series(100 * np.exp(np.cumsum(np.full(600, 0.0005))), index=idx)
    values.iloc[100:] *= 1.4  # 2018'de gerçek bir kur şoku gibi kalıcı sıçrama
    report = assess_series("X", values, as_of=idx[-1])
    assert report["issues"] and report["issues"][0]["severity"] == "info"
    assert report["status"] == "ok"


def test_snapshot_marks_proxy_segments() -> None:
    from services.market_data.sources import SnapshotSource

    series = SnapshotSource().meta()["series"]
    assert series["TL_PPF"]["has_proxy_segment"] is True and series["TL_PPF"]["proxy_until"]
    assert series["XU100.IS"]["has_proxy_segment"] is False


def test_realized_pnl_is_net_of_sale_costs(client: TestClient) -> None:
    from decimal import Decimal

    from core.database import session_factory
    from models import Portfolio
    from services.ledger import apply_trade
    from tests.conftest import create_customer, create_portfolio

    cid = create_customer(client, email="netkar@example.com")["id"]
    pid = create_portfolio(client, cid, cash=10_000.0, holdings={})["id"]

    async def run() -> Decimal:
        async with session_factory() as s:
            p = await s.get(Portfolio, pid)
            await apply_trade(s, p, symbol="TL_PPF", side="BUY", quantity=100, price=10)
            await s.flush()
            res = await apply_trade(
                s, p, symbol="TL_PPF", side="SELL", quantity=100, price=12, fees=5
            )
            await s.commit()
            return res.realized_pnl

    assert client.portal.call(run) == Decimal("195")  # 100 × 2 − 5 TL satış maliyeti
