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
