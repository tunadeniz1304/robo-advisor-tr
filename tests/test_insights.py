"""F6: explanations, regime, stress lab, BL house views, Autopilot, nudges."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.market_data.service import MarketDataService
from services.market_data.sources import ChainedSource, SnapshotSource
from services.regime import LABELS, detect_regime
from services.stress import run_stress
from tests.conftest import auth_headers, create_customer, create_portfolio


@pytest.fixture(scope="module")
def market() -> MarketDataService:
    return MarketDataService(ChainedSource(None, SnapshotSource(), mode="snapshot"))


async def test_regime_detection_is_labelled_and_bounded(market: MarketDataService) -> None:
    r = await detect_regime(market)
    assert r["label"] in LABELS and abs(r["tilt"]) <= 0.05 + 1e-12
    assert r["method"] in {"hmm", "volatilite_esigi"}
    assert r["probabilities"] and abs(sum(r["probabilities"].values()) - 1.0) < 1e-6
    again = await detect_regime(market)
    assert again["label"] == r["label"]  # deterministik (sabit tohum)


async def test_regime_fallback_without_hmmlearn(
    market: MarketDataService, monkeypatch: pytest.MonkeyPatch
) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        if name.startswith("hmmlearn"):
            raise ImportError("yok")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    r = await detect_regime(market)
    assert r["method"] == "volatilite_esigi" and r["label"] in LABELS


async def test_factor_and_historical_stress(market: MarketDataService) -> None:
    values = {"XU100.IS": 50_000.0, "USDTRY": 30_000.0, "TL_PPF": 20_000.0}
    bist = await run_stress(market, values, 0.0, scenario="bist_down_25")
    assert bist["pnl"] < 0 and bist["katkilar"]["XU100.IS"] == pytest.approx(-12_500, rel=0.1)
    fx = await run_stress(market, values, 0.0, scenario="usdtry_up_30")
    assert fx["katkilar"]["USDTRY"] > 0
    hist = await run_stress(market, values, 0.0, scenario="kur_soku_2018")
    assert hist["tur"] == "tarihsel" and hist["katkilar"]["USDTRY"] > 0 and hist["maks_dusus"] <= 0
    custom = await run_stress(market, values, 0.0, custom={"bist": -0.1, "usdtry": 0.1})
    assert custom["senaryo_adi"] == "Özel senaryo"
    with pytest.raises(ValueError):
        await run_stress(market, values, 0.0, scenario="yok")


def _executed_portfolio(client: TestClient) -> tuple[int, int, int]:
    cid = create_customer(client)["id"]
    pid = create_portfolio(client, cid, cash=200_000.0, holdings={})["id"]
    prop = client.post(f"/api/v1/advisor/rebalance/{pid}", params={"customer_id": cid}).json()
    client.post(f"/api/v1/proposals/{prop['proposal_id']}/approve")
    return cid, pid, prop["proposal_id"]


def test_explain_regime_and_stress_api(client: TestClient) -> None:
    cid, pid, prop_id = _executed_portfolio(client)
    ex = client.get(f"/api/v1/proposals/{prop_id}/explain").json()
    codes = [c["kod"] for c in ex["kartlar"]]
    assert codes[:2] == ["profil", "model"] and "risk_degisimi" in codes and "maliyet" in codes
    assert ex["metin"] and ex["llm_mode"] == "demo"
    assert client.get("/api/v1/regime").json()["label"] in LABELS
    assert "historical" in client.get("/api/v1/stress/scenarios").json()
    client.post(
        f"/api/v1/customers/{cid}/goals",
        json={
            "goal_type": "ev",
            "name": "Ev",
            "target_amount_real": 2_000_000,
            "horizon_years": 8,
            "initial_amount": 200_000,
        },
    )
    res = client.post(f"/api/v1/portfolios/{pid}/stress", json={"scenario": "bist_down_25"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["pnl"] <= 0 and body["hedef_etkisi"]
    assert body["hedef_etkisi"][0]["sonra"] <= body["hedef_etkisi"][0]["once"]


def test_bl_views_flow_changes_bl_optimisation(client: TestClient) -> None:
    created = client.post(
        "/api/v1/bl-views",
        json={
            "symbol": "ALTIN_TL",
            "expected_return": 0.9,
            "confidence": 0.8,
            "rationale": "Ev görüşü",
        },
    )
    assert created.status_code == 201 and created.json()["status"] == "onaylandi"
    suggested = client.post("/api/v1/bl-views/suggest").json()
    assert all(v["status"] == "onerildi" and v["source"] == "llm" for v in suggested)
    if suggested:
        assert (
            client.post(f"/api/v1/bl-views/{suggested[0]['id']}/reject").json()["status"]
            == "reddedildi"
        )
    cid = create_customer(client)["id"]
    opt = client.post(
        f"/api/v1/customers/{cid}/optimize", json={"method": "black_litterman"}
    ).json()
    assert opt["bl"]["views"][0]["symbol"] == "ALTIN_TL"
    assert opt["bl"]["posterior_returns"]["ALTIN_TL"] > opt["bl"]["prior_returns"]["ALTIN_TL"]
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "username": "blm",
            "password": "GucluSifre!1",
            "full_name": "B L",
            "email": "b@l.com",
        },
    ).json()
    assert (
        client.get("/api/v1/bl-views", headers=auth_headers(reg["access_token"])).status_code == 403
    )


def test_autopilot_sweep_proposal_and_auto_mode(client: TestClient) -> None:
    cid = create_customer(client)["id"]
    pid = create_portfolio(client, cid, cash=50_000.0, holdings={"TL_PPF": 10.0})["id"]
    settings = client.get(f"/api/v1/portfolios/{pid}/autopilot").json()
    assert settings["enabled"] is False and "Çıkar çatışması" in settings["disclosure"]
    client.put(
        f"/api/v1/portfolios/{pid}/autopilot", json={"enabled": True, "cash_threshold": 10_000}
    )
    sw = client.post(f"/api/v1/portfolios/{pid}/autopilot/sweep").json()
    assert sw["swept"] and not sw["executed"]
    prop = client.get(f"/api/v1/proposals/{sw['proposal_id']}").json()
    assert prop["status"] == "ONAY_BEKLIYOR" and prop["orders"][0]["symbol"] == "TL_PPF"
    client.put(f"/api/v1/portfolios/{pid}/autopilot", json={"mode": "otomatik"})
    sw2 = client.post(f"/api/v1/portfolios/{pid}/autopilot/sweep").json()
    assert sw2["executed"]
    assert client.get(f"/api/v1/portfolios/{pid}").json()["cash"] < 11_000
    assert client.post(f"/api/v1/portfolios/{pid}/autopilot/sweep").json()["swept"] is False


def test_nudges_and_behavior_gap(client: TestClient) -> None:
    cid = create_customer(client)["id"]
    create_portfolio(client, cid, cash=90_000.0, holdings={"TL_PPF": 10.0})
    items = client.get(f"/api/v1/customers/{cid}/nudges").json()
    kinds = {n["kind"] for n in items}
    assert {"atil_nakit", "profil_yenileme"} <= kinds
    again = client.get(f"/api/v1/customers/{cid}/nudges").json()
    assert len(again) == len(items)  # soğuma süresi içinde tekrar üretilmez
    dismissed = client.post(f"/api/v1/nudges/{items[0]['id']}/dismiss").json()
    assert dismissed["dismissed_at"]
    gap = client.get(f"/api/v1/customers/{cid}/behavior-gap").json()
    assert gap["portfolios"] and "behavior_gap" in gap["portfolios"][0]


def test_scheduler_extra_jobs(client: TestClient) -> None:
    from core.scheduler import autopilot_job, nudges_job

    container = client.app.state.container  # type: ignore[attr-defined]
    cid = create_customer(client)["id"]
    pid = create_portfolio(client, cid, cash=60_000.0, holdings={"TL_PPF": 1.0})["id"]
    client.put(
        f"/api/v1/portfolios/{pid}/autopilot", json={"enabled": True, "cash_threshold": 5_000}
    )
    assert client.portal.call(autopilot_job, container) == 1
    assert client.portal.call(nudges_job, container) >= 1
