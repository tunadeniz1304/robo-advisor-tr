"""v2 audit finding A.6: walk-forward backtest without look-ahead bias.

The optimiser used by the backtest may only see prices up to each rebalance
date. Changing *future* prices must never change *past* weights.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient


def _prices(n: int = 1_300, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2019-01-01", periods=n)
    vols = {"TL_PPF": 0.001, "TL_TAHVIL": 0.004, "ALTIN_TL": 0.012, "XU100.IS": 0.018}
    data = {s: 100 * np.exp(np.cumsum(rng.normal(0.0006, v, n))) for s, v in vols.items()}
    return pd.DataFrame(data, index=idx)


def _inverse_vol(rets: pd.DataFrame) -> dict[str, float]:
    iv = 1.0 / rets.std().clip(lower=1e-9)
    return (iv / iv.sum()).to_dict()


def test_optimizer_never_sees_future_data() -> None:
    from services.analytics.walkforward import walk_forward

    seen: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    prices = _prices()

    def spy(rets: pd.DataFrame, as_of: pd.Timestamp) -> dict[str, float]:
        seen.append((rets.index.max(), as_of))
        return _inverse_vol(rets)

    res = walk_forward(prices, spy, window_days=504, rebalance_days=63)
    assert len(seen) >= 10
    assert all(last <= as_of for last, as_of in seen)
    assert res["mode"] == "walk_forward"
    first_rebalance = pd.Timestamp(res["weights_history"][0]["date"])
    assert first_rebalance >= prices.index[504]


def test_changing_future_prices_does_not_change_past_weights() -> None:
    from services.analytics.walkforward import walk_forward

    prices = _prices()
    cut = prices.index[900]
    shocked = prices.copy()
    shocked.loc[shocked.index > cut, "XU100.IS"] *= np.linspace(
        1.0, 3.0, int((shocked.index > cut).sum())
    )

    def opt(rets: pd.DataFrame, as_of: pd.Timestamp) -> dict[str, float]:
        return _inverse_vol(rets)

    base = walk_forward(prices, opt, window_days=504, rebalance_days=63)
    alt = walk_forward(shocked, opt, window_days=504, rebalance_days=63)
    past_base = [w for w in base["weights_history"] if pd.Timestamp(w["date"]) <= cut]
    past_alt = [w for w in alt["weights_history"] if pd.Timestamp(w["date"]) <= cut]
    assert past_base and past_base == past_alt
    future_base = [w for w in base["weights_history"] if pd.Timestamp(w["date"]) > cut]
    future_alt = [w for w in alt["weights_history"] if pd.Timestamp(w["date"]) > cut]
    assert future_base != future_alt  # gelecekte tabii ki farklılaşır


def test_walk_forward_reports_benchmarks() -> None:
    from services.analytics.walkforward import walk_forward

    prices = _prices()

    def opt(rets: pd.DataFrame, as_of: pd.Timestamp) -> dict[str, float]:
        return _inverse_vol(rets)

    cpi = pd.Series(
        100 * 1.02 ** np.arange(80), index=pd.date_range("2018-12-31", periods=80, freq="ME")
    )
    res = walk_forward(
        prices,
        opt,
        window_days=504,
        rebalance_days=63,
        benchmark_prices=prices[["XU100.IS", "TL_TAHVIL"]],
        cpi=cpi,
    )
    names = set(res["benchmarks"])
    assert {"XU100", "TUFE+3", "60/40"} <= names
    for bench in res["benchmarks"].values():
        assert bench["stats"]["start"] == res["stats"]["start"]  # aynı test dönemi


def test_backtest_api_uses_walk_forward_for_optimizer(settings) -> None:  # noqa: ANN001
    from core.app import create_app
    from tests.conftest import FakeMarketSource, auth_headers, login

    app = create_app(settings=settings, market_source=FakeMarketSource(n_days=1_600))
    with TestClient(app) as client:
        client.headers.update(auth_headers(login(client)))
        resp = client.post(
            "/api/v1/backtest",
            json={"level": 5, "use_optimizer": True, "method": "hrp", "compare": False, "years": 5},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "walk_forward"
    assert body["window_days"] > 0 and body["rebalance_days"] > 0
    assert "benchmarks" in body
    first = body["weights_history"][0]["date"]
    assert first > body["data_start"]  # ilk ağırlık, tahmin penceresi dolduktan sonra
    # Denetim tur 3: yanıt gerçekleşen test süresini ve testteki vekil serileri bildirir.
    span = (pd.Timestamp(body["test_end"]) - pd.Timestamp(body["test_start"])).days / 365.25
    assert body["years_requested"] == 5
    assert body["years"] == pytest.approx(span, abs=0.01) and body["years"] <= 5
    assert body["proxy_in_test"] == []  # sahte veri kaynağında vekil yok


def test_proxy_segments_inside_a_test_period_are_reported() -> None:
    from routers.reports import _proxy_until
    from services.market_data.service import MarketDataService
    from services.market_data.sources import ChainedSource, SnapshotSource

    market = MarketDataService(ChainedSource(None, SnapshotSource(), mode="snapshot"))
    assert _proxy_until(market, "TL_PPF") is not None
    assert _proxy_until(market, "XU100.IS") is None


@pytest.mark.parametrize("window", [0, -5])
def test_walk_forward_rejects_bad_window(window: int) -> None:
    from services.analytics.walkforward import walk_forward

    with pytest.raises(ValueError):
        walk_forward(_prices(), lambda r, d: _inverse_vol(r), window_days=window, rebalance_days=63)
