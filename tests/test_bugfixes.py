"""Regression tests for the 14 known defects listed in the transformation plan."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from core.app import create_app
from core.config import BASE_DIR
from core.money import Money, to_decimal
from llm.clients import LLMClient, LLMProviderError, LLMResponse
from services.analytics_service import AnalyticsService
from services.portfolio_service import PortfolioService
from tests.conftest import (
    FakeMarketSource,
    auth_headers,
    create_customer,
    create_portfolio,
    login,
    make_settings,
    stable_seed,
)


class FailingLiveLLM(LLMClient):
    """Live-mode client that always times out."""

    mode = "live"
    model = "failing"

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        self.calls += 1
        raise LLMProviderError("zaman aşımı", kind="timeout")


class CountingSource(FakeMarketSource):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def download_history(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        self.calls += 1
        return await super().download_history(symbols)


def _app_client(tmp_path: Path, **kwargs: Any) -> TestClient:
    app = create_app(make_settings(tmp_path), **kwargs)
    return TestClient(app)


# -- #1 çift rebalance ---------------------------------------------------------


def test_bug01_llm_failure_rebalances_exactly_once(tmp_path: Path) -> None:
    failing = FailingLiveLLM()
    with _app_client(tmp_path, market_source=FakeMarketSource(), llm_client=failing) as c:
        c.headers.update(auth_headers(login(c)))
        cid = create_customer(c)["id"]
        pid = create_portfolio(c, cid)["id"]
        resp = c.post(f"/api/v1/advisor/rebalance/{pid}", params={"customer_id": cid})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["llm_mode"] == "fallback"
        assert body["orders"], "gerçek emirler öneride olmalı"
        assert len(c.get("/api/v1/proposals", params={"portfolio_id": pid}).json()) == 1
        done = c.post(f"/api/v1/proposals/{body['proposal_id']}/approve").json()
        ledger = c.get("/api/v1/transactions", params={"portfolio_id": pid}).json()
        assert len(ledger) == len(done["execution_report"]["fills"])  # ikinci rebalance yok
        runs = c.get("/api/v1/runs", params={"portfolio_id": pid}).json()
        assert len(runs) == 1 and runs[0]["status"] == "degraded"
    assert failing.calls == 1  # canlı hatada doğrudan fallback, tekrar yok


# -- #2 performans ---------------------------------------------------------------


def test_bug02_total_return_is_compounded() -> None:
    frame = pd.DataFrame({"A": [0.10, 0.10]})
    m = AnalyticsService().performance_metrics(frame)
    assert m["total_return"] == pytest.approx(0.21)


def test_bug02_uses_actual_weights_not_equal() -> None:
    idx = pd.bdate_range("2024-01-01", periods=10)
    frame = pd.DataFrame({"A": [0.01] * 10, "B": [0.0] * 10}, index=idx)
    m = AnalyticsService().performance_metrics(frame, weights={"A": 0.25, "B": 0.75})
    assert m["total_return"] == pytest.approx(1.0025**10 - 1.0, abs=1e-6)
    eq = AnalyticsService().performance_metrics(frame)
    assert eq["total_return"] == pytest.approx(1.005**10 - 1.0, abs=1e-6)


# -- #3 tangency -----------------------------------------------------------------


def test_bug03_tangency_uses_real_risk_free_rate() -> None:
    rng = np.random.default_rng(3)
    n = 750
    frame = pd.DataFrame(
        {
            "YUKSEK": rng.normal(0.0020, 0.012, n),  # ~%50/yıl
            "ORTA": rng.normal(0.0012, 0.010, n),  # ~%30/yıl
            "DUSUK": rng.normal(0.0002, 0.008, n),  # ~%5/yıl
        }
    )
    svc = PortfolioService()
    w_rf0 = svc.tangency_weights(frame, risk_free_rate=0.0)
    w_rf40 = svc.tangency_weights(frame, risk_free_rate=0.40)
    assert sum(w_rf40.values()) == pytest.approx(1.0)
    assert all(v >= 0 for v in w_rf40.values())
    # %40 TL faizinde düşük getirili varlığın ağırlığı sıfıra iner/azalır
    assert w_rf40["DUSUK"] <= w_rf0["DUSUK"] + 1e-9
    assert w_rf40["DUSUK"] < 0.05


def test_bug03_all_below_rf_falls_back_to_min_variance() -> None:
    rng = np.random.default_rng(4)
    frame = pd.DataFrame({"A": rng.normal(0.0001, 0.01, 300), "B": rng.normal(0.0001, 0.02, 300)})
    w = PortfolioService().tangency_weights(frame, risk_free_rate=0.45)
    assert w["A"] > w["B"]  # daha düşük oynaklık → min-varyansta daha yüksek ağırlık


# -- #4 drift stub ----------------------------------------------------------------


def test_bug04_drift_uses_real_target(client: TestClient) -> None:
    cid = create_customer(client)["id"]
    pid = create_portfolio(client, cid)["id"]
    assert client.get(f"/api/v1/portfolios/{pid}/drift").status_code == 409
    body = client.post(f"/api/v1/advisor/rebalance/{pid}", params={"customer_id": cid}).json()
    pending = client.get(f"/api/v1/portfolios/{pid}/drift").json()
    assert pending["needs_rebalance"] is True
    targets = {row["symbol"]: row["target_weight"] for row in pending["assets"]}
    for sym, w in body["weights"].items():
        assert targets[sym] == pytest.approx(w, abs=1e-6)
    client.post(f"/api/v1/proposals/{body['proposal_id']}/approve")
    assert client.get(f"/api/v1/portfolios/{pid}/drift").json()["needs_rebalance"] is False


# -- #5 projeksiyon ----------------------------------------------------------------


def test_bug05_projection_uses_statistical_drift(client: TestClient) -> None:
    cid = create_customer(client)["id"]
    pid = create_portfolio(client, cid, cash=0.0, holdings={"AAA.IS": 10.0})["id"]
    proj = client.get(f"/api/v1/portfolios/{pid}/projection", params={"horizon_years": 5}).json()
    frame = asyncio_frame("AAA.IS")
    expected = PortfolioService().expected_returns(frame).iloc[0]
    assert proj["annual_return"] == pytest.approx(expected, rel=1e-4)
    assert proj["annual_volatility"] > 0


def asyncio_frame(symbol: str) -> pd.DataFrame:
    import asyncio

    hist = asyncio.run(FakeMarketSource().download_history([symbol]))[symbol]
    return hist[["Close"]].pct_change().dropna().rename(columns={"Close": symbol})


# -- #6 manuel işlem --------------------------------------------------------------


def test_bug06_manual_transaction_updates_holdings_and_cash(client: TestClient) -> None:
    cid = create_customer(client)["id"]
    pid = create_portfolio(client, cid, cash=10_000.0, holdings={})["id"]
    buy = {
        "portfolio_id": pid,
        "ticker": "thyao.is",
        "side": "BUY",
        "quantity": 10,
        "price": 300.0,
        "fees": 6.0,
    }
    assert client.post("/api/v1/transactions", json=buy).status_code == 201
    pf = client.get(f"/api/v1/portfolios/{pid}").json()
    assert pf["holdings"] == {"THYAO.IS": 10.0}
    assert pf["cash"] == pytest.approx(10_000 - 3_000 - 6)

    sell = {**buy, "side": "SELL", "quantity": 4, "price": 310.0, "fees": 0.0}
    assert client.post("/api/v1/transactions", json=sell).status_code == 201
    pf = client.get(f"/api/v1/portfolios/{pid}").json()
    assert pf["holdings"] == {"THYAO.IS": 6.0}
    assert pf["cash"] == pytest.approx(6_994 + 1_240)

    too_much = {**sell, "quantity": 100}
    assert client.post("/api/v1/transactions", json=too_much).status_code == 422
    no_cash = {**buy, "quantity": 1000}
    assert client.post("/api/v1/transactions", json=no_cash).status_code == 422
    assert len(client.get("/api/v1/transactions", params={"portfolio_id": pid}).json()) == 2


# -- #7 paylaşılan cache ------------------------------------------------------------


def test_bug07_market_cache_shared_across_requests(tmp_path: Path) -> None:
    source = CountingSource()
    with _app_client(tmp_path, market_source=source) as c:
        c.headers.update(auth_headers(login(c)))
        cid = create_customer(c)["id"]
        pid = create_portfolio(c, cid)["id"]
        for _ in range(3):
            assert c.get(f"/api/v1/portfolios/{pid}/valuation").status_code == 200
    assert source.calls == 1


# -- #10 deterministik seed -----------------------------------------------------------


async def test_bug10_fake_source_is_deterministic() -> None:
    a = await FakeMarketSource().download_history(["THYAO.IS"])
    b = await FakeMarketSource().download_history(["THYAO.IS"])
    pd.testing.assert_frame_equal(a["THYAO.IS"], b["THYAO.IS"])


def test_bug10_seed_is_crc32() -> None:
    import zlib

    assert stable_seed("AKBNK.IS") == zlib.crc32(b"AKBNK.IS")


# -- #11 bağımlılık ve ölü kod --------------------------------------------------------


def test_bug11_email_validator_declared_and_dead_prompts_removed() -> None:
    import importlib.util

    assert importlib.util.find_spec("email_validator") is not None
    reqs = (BASE_DIR / "requirements.txt").read_text(encoding="utf-8")
    assert "email-validator" in reqs
    assert importlib.util.find_spec("agents.prompts") is None


def test_bug11_disclaimer_is_used(client: TestClient) -> None:
    cid = create_customer(client)["id"]
    pid = create_portfolio(client, cid)["id"]
    report = client.post(f"/api/v1/advisor/rebalance/{pid}", params={"customer_id": cid}).json()[
        "report"
    ]
    assert "yatırım tavsiyesi değildir" in report


# -- #12 Decimal para -----------------------------------------------------------------


def test_bug12_money_is_exact_decimal(client: TestClient) -> None:
    from sqlalchemy import text

    from core.database import session_factory

    cid = create_customer(client)["id"]
    pid = create_portfolio(client, cid, cash=1234567.123456, holdings={})["id"]

    async def _raw() -> Any:
        async with session_factory() as s:
            return (
                await s.execute(text("SELECT cash FROM portfolios WHERE id=:i"), {"i": pid})
            ).scalar_one()

    assert client.portal.call(_raw) == "1234567.123456"
    assert to_decimal(0.1) + to_decimal(0.2) == Decimal("0.3")
    assert isinstance(Money().process_result_value("1.5", _Sqlite()), Decimal)


class _Sqlite:
    name = "sqlite"


# -- #13 compose güvenliği --------------------------------------------------------------


def test_bug13_compose_has_no_hardcoded_password_or_public_db_port() -> None:
    compose = (BASE_DIR / "docker-compose.yml").read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD: advisor" not in compose
    assert "5432:5432" not in compose
    assert "env_file" in compose


# -- #14 graf bir kez derlenir ------------------------------------------------------------


async def test_bug14_graph_compiled_once_with_durable_checkpointer(tmp_path: Path) -> None:
    from services.advisor_service import AdvisorService

    service = AdvisorService(make_settings(tmp_path), checkpoint_db=str(tmp_path / "cp.sqlite"))
    try:
        g1 = await service.graph()
        g2 = await service.graph()
        assert g1 is g2
        assert type(g1.checkpointer).__name__ == "AsyncSqliteSaver"
    finally:
        await service.aclose()


def test_bug14_container_uses_configured_checkpoint_db(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, checkpoint_db=str(tmp_path / "cp.sqlite"))
    app = create_app(settings, market_source=FakeMarketSource())
    assert app.state.container.advisor._checkpoint_db == str(tmp_path / "cp.sqlite")
