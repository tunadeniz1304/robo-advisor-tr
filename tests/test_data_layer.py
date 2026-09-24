"""F2 tests: migrations, offline snapshot, market/macro data layer, audit chain,
reference data and the market API."""

from __future__ import annotations

import math
import socket
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from core.app import create_app
from core.database import alembic_config, run_migrations
from services.market_data.derive import (
    TROY_OUNCE_GRAMS,
    bond_fund_series,
    derive_instruments,
    money_market_series,
)
from services.market_data.service import MarketDataService, deflate, log_returns, simple_returns
from services.market_data.sources import ChainedSource, SnapshotSource
from services.market_data.universe import BY_SYMBOL, UNIVERSE, asset_class_of
from tests.conftest import auth_headers, create_customer, make_settings

# ------------------------------------------------------------------ migrations


def test_migrations_roundtrip_and_no_drift(tmp_path: Path) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine

    import models  # noqa: F401
    from alembic import command
    from core.database import Base

    url = f"sqlite+aiosqlite:///{(tmp_path / 'm.db').as_posix()}"
    run_migrations(url)
    sync = create_engine(f"sqlite:///{(tmp_path / 'm.db').as_posix()}")
    with sync.connect() as conn:
        diff = compare_metadata(
            MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata
        )
    assert diff == [], diff
    command.downgrade(alembic_config(url), "base")
    with sync.connect() as conn:
        tables = {
            r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }
    assert tables <= {"alembic_version"}
    run_migrations(url)
    sync.dispose()


def test_app_runs_migrations_on_startup(tmp_path: Path) -> None:
    db = tmp_path / "fresh.db"
    settings = make_settings(
        tmp_path, auto_migrate=True, database_url=f"sqlite+aiosqlite:///{db.as_posix()}"
    )
    with TestClient(create_app(settings)) as c:
        assert c.get("/health/ready").json()["checks"]["database"] == "ok"
    import sqlite3

    with sqlite3.connect(db) as conn:
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert version == "0001"
    assert {"rebalance_proposals", "audit_log", "goals", "risk_profiles", "instruments"} <= tables


# ------------------------------------------------------------------ snapshot


def test_snapshot_is_complete_and_offline() -> None:
    source = SnapshotSource()
    panel = source.panel()
    assert set(panel.columns) == {s.symbol for s in UNIVERSE}
    assert panel.shape[0] > 252 * 5  # ≥5 yıl günlük
    # v2: yalnız TEFAS'ın verdiği 5 yıla sahip fonlar ilk tarihlerinden önce boştur;
    # sınıf temsilcileri ve piyasa serileri tam uzunluktadır, hiçbir seride iç boşluk yoktur.
    series = source.meta()["series"]
    for sym in panel.columns:
        col = panel[sym]
        first = col.first_valid_index()
        assert not col.loc[first:].isna().any(), sym
        if BY_SYMBOL[sym].splice or BY_SYMBOL[sym].source != "tefas":
            assert first == panel.index[0], sym
        assert series[sym]["first_date"] == first.date().isoformat()
    macro = source.macro()
    assert {"TUFE", "POLICY_RATE"} <= set(macro.columns)
    assert source.meta()["instruments"]


async def test_snapshot_source_protocol() -> None:
    frames = await SnapshotSource().download_history(["XU100.IS", "YOK.IS"])
    assert not frames["XU100.IS"].empty and "Close" in frames["XU100.IS"].columns
    assert frames["YOK.IS"].empty


def test_network_is_blocked_in_tests() -> None:
    with pytest.raises(RuntimeError, match="ağ erişimi yasak"):
        socket.create_connection(("example.com", 80), timeout=1)


# ------------------------------------------------------------------ derivation


def test_money_market_accrual_matches_formula() -> None:
    idx = pd.DatetimeIndex(["2024-01-01", "2024-01-02", "2024-01-05"])
    series = money_market_series(pd.Series(0.365, index=idx), fee=0.0)
    assert series.iloc[1] / series.iloc[0] == pytest.approx(1.365 ** (1 / 365))
    assert series.iloc[2] / series.iloc[1] == pytest.approx(1.365 ** (3 / 365))


def test_bond_fund_loses_on_rate_hike() -> None:
    idx = pd.bdate_range("2024-01-01", periods=5)
    rate = pd.Series([0.40, 0.40, 0.45, 0.45, 0.45], index=idx)
    series = bond_fund_series(rate, fee=0.0, duration=2.0)
    assert series.iloc[2] < series.iloc[1]  # +500bp × 2 yıl süre ≈ −%10


def test_derived_gold_and_eurobond() -> None:
    from services.market_data.derive import proxy_series

    idx = pd.bdate_range("2024-01-01", periods=3)
    raw = pd.DataFrame({"GC=F": [2000.0] * 3, "USDTRY=X": [30.0] * 3, "EMB": [90.0] * 3}, index=idx)
    panel = derive_instruments(raw, None)
    assert panel["ALTIN_TL"].iloc[0] == pytest.approx(2000 * 30 / TROY_OUNCE_GRAMS)
    # v2: fonlar canlı panelde türetilmez (gerçek TEFAS fiyatı snapshot'tan gelir);
    # eurobond vekili yalnızca TEFAS öncesi dönemi eklemek için kullanılır.
    assert "TL_PPF" not in panel and "EUROBOND_TL" not in panel
    assert proxy_series("eurobond", "EUROBOND_TL", raw, pd.DataFrame()).iloc[0] == pytest.approx(
        2700.0
    )


def test_universe_metadata() -> None:
    assert len(UNIVERSE) >= 20
    assert asset_class_of("THYAO.IS") == "bist_hisse"
    assert asset_class_of("TL_PPF") == "para_piyasasi"
    assert (
        BY_SYMBOL["TL_PPF"].risk_score
        < BY_SYMBOL["XU100.IS"].risk_score
        < BY_SYMBOL["THYAO.IS"].risk_score
    )


# ------------------------------------------------------------------ chain


class _LiveOk:
    async def download_history(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        idx = pd.bdate_range("2026-01-01", periods=30)
        return {s: pd.DataFrame({"Close": np.linspace(10, 11, 30)}, index=idx) for s in symbols[:1]}


class _LiveDown:
    async def download_history(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        raise RuntimeError("ağ yok")


async def test_chain_falls_back_to_snapshot() -> None:
    chain = ChainedSource(_LiveDown(), SnapshotSource(), mode="auto")
    frames = await chain.download_history(["XU100.IS"])
    assert not frames["XU100.IS"].empty
    assert chain.last_source == "snapshot" and chain.last_error


async def test_chain_mixed_and_live_modes() -> None:
    chain = ChainedSource(_LiveOk(), SnapshotSource(), mode="auto")
    await chain.download_history(["XU100.IS", "ALTIN_TL"])
    assert chain.last_source == "mixed"
    strict = ChainedSource(_LiveDown(), SnapshotSource(), mode="live")
    with pytest.raises(RuntimeError):
        await strict.download_history(["XU100.IS"])


# ------------------------------------------------------------------ service


async def test_returns_conventions() -> None:
    svc = MarketDataService(ChainedSource(None, SnapshotSource(), mode="snapshot"))
    simple = await svc.returns(["XU100.IS"])
    logr = await svc.returns(["XU100.IS"], kind="log")
    np.testing.assert_allclose(np.log1p(simple.to_numpy()), logr.to_numpy(), atol=1e-12)
    usd = await svc.returns(["XU100.IS"], currency="USD", freq="M")
    tl = await svc.returns(["XU100.IS"], freq="M")
    assert usd["XU100.IS"].mean() < tl["XU100.IS"].mean()  # TL değer kaybı
    real = await svc.returns(["XU100.IS"], freq="M", real=True)
    assert real["XU100.IS"].mean() < tl["XU100.IS"].mean()
    with pytest.raises(ValueError):
        await svc.returns(["XU100.IS"], real=True)


def test_deflate_formula() -> None:
    idx = pd.date_range("2024-01-31", periods=3, freq="ME")
    cpi = pd.Series([100.0, 102.0, 104.04], index=idx)
    rets = pd.DataFrame({"A": [0.0, 0.02, 0.05]}, index=idx)
    real = deflate(rets, cpi)
    assert real["A"].iloc[1] == pytest.approx(0.0)
    assert real["A"].iloc[2] == pytest.approx(1.05 / 1.02 - 1.0)


def test_simple_and_log_helpers() -> None:
    prices = pd.DataFrame({"A": [100.0, 110.0, 99.0]})
    assert simple_returns(prices)["A"].tolist() == pytest.approx([0.1, -0.1])
    assert log_returns(prices)["A"].iloc[0] == pytest.approx(math.log(1.1))


def test_macro_rates() -> None:
    svc = MarketDataService(ChainedSource(None, SnapshotSource(), mode="snapshot"))
    assert 0.05 < svc.risk_free_rate() < 0.8
    assert 0.0 < svc.inflation_yoy() < 1.0
    assert svc.data_status()["source"] == "snapshot"


async def test_persist_prices_writes_live_rows(client: TestClient) -> None:
    market: MarketDataService = client.app.state.container.market  # type: ignore[attr-defined]
    chain = ChainedSource(_LiveOk(), SnapshotSource(), mode="auto")
    svc = MarketDataService(chain)

    async def _run() -> int:
        await svc.history(["XU100.IS"])
        return await svc.persist_prices(days=5)

    written = client.portal.call(_run)
    assert written > 0
    assert market is not None


# ------------------------------------------------------------------ audit chain


def test_audit_chain_valid_then_detects_tampering(client: TestClient) -> None:
    create_customer(client)
    create_customer(client, email="iki@example.com")
    status = client.get("/api/v1/audit/verify").json()
    assert status["valid"] is True and status["count"] >= 3  # login + 2 müşteri
    logs = client.get("/api/v1/audit", params={"action": "customer.create"}).json()
    assert len(logs) == 2 and logs[0]["prev_hash"] != logs[0]["hash"]

    from core.database import session_factory

    async def _tamper() -> None:
        async with session_factory() as s:
            await s.execute(text("UPDATE audit_log SET payload='{\"hack\": 1}' WHERE id=2"))
            await s.commit()

    client.portal.call(_tamper)
    broken = client.get("/api/v1/audit/verify").json()
    assert broken["valid"] is False and broken["broken_at"] == 2


def test_audit_requires_staff(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "username": "denetci",
            "password": "GucluSifre!1",
            "full_name": "D K",
            "email": "d@k.com",
        },
    )
    h = auth_headers(resp.json()["access_token"])
    assert client.get("/api/v1/audit", headers=h).status_code == 403
    assert client.get("/api/v1/audit/verify", headers=h).status_code == 403


# ------------------------------------------------------------------ reference / API


def test_reference_data_seeded_and_market_api(client: TestClient) -> None:
    instruments = client.get("/api/v1/market/instruments").json()
    assert len(instruments) == len(UNIVERSE)
    assert {i["asset_class"] for i in instruments} >= {"para_piyasasi", "altin", "bist_hisse"}
    models: list[dict[str, Any]] = client.get("/api/v1/market/model-portfolios").json()
    assert [m["level"] for m in models] == list(range(1, 11))
    assert all(abs(sum(m["class_weights"].values()) - 1.0) < 1e-9 for m in models)
    macro = client.get("/api/v1/market/macro", params={"months": 12}).json()
    assert len(macro["series"]) == 12 and macro["risk_free_rate"] > 0
    prices = client.get(
        "/api/v1/market/prices", params={"symbols": "XU100.IS,ALTIN_TL", "days": 30}
    ).json()
    assert len(prices["dates"]) == 30 and set(prices["series"]) == {"XU100.IS", "ALTIN_TL"}
    usd = client.get(
        "/api/v1/market/prices", params={"symbols": "XU100.IS", "days": 5, "currency": "USD"}
    ).json()
    assert usd["series"]["XU100.IS"][-1] < prices["series"]["XU100.IS"][-1]
    assert client.get("/api/v1/market/status").json()["source"] in {"snapshot", "custom"}


def test_reference_seed_is_idempotent(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    for _ in range(2):
        with TestClient(create_app(settings)) as c:
            pass
    import sqlite3

    db = settings.database_url.split("///", 1)[1]
    with sqlite3.connect(db) as conn:
        n_inst = conn.execute("SELECT COUNT(*) FROM instruments").fetchone()[0]
        n_models = conn.execute("SELECT COUNT(*) FROM model_portfolios").fetchone()[0]
    assert n_inst == len(UNIVERSE) and n_models == 10
    assert c is not None
