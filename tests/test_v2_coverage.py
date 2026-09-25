"""v2 quality gate: behaviour of modules that were under 80 % coverage.

Logging configuration, Redis client factories, money edge cases, run and
transaction ownership (404 for foreign records), the live Yahoo / chained
sources and the Anthropic client path — all offline.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from core.money import round_cents, to_decimal, to_float
from tests.conftest import auth_headers, create_customer, create_portfolio

# ---------------------------------------------------------------- logging ---


@pytest.mark.parametrize("log_json", [True, False])
def test_configure_logging_renders(log_json: bool, capsys: pytest.CaptureFixture[str]) -> None:
    import structlog

    from core.logging import configure_logging, get_logger

    configure_logging("NOT-A-LEVEL", log_json=log_json)
    get_logger("test.v2").warning("olay", tutar=1)
    out = capsys.readouterr().out
    if log_json:
        record = json.loads(out.strip().splitlines()[-1])
        assert record["event"] == "olay" and record["tutar"] == 1
    else:
        assert "olay" in out
    structlog.reset_defaults()


def test_configure_logging_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import structlog

    from core.logging import configure_logging_from_env

    monkeypatch.setenv("LOG_LEVEL", "warning")
    monkeypatch.setenv("LOG_JSON", "true")
    configure_logging_from_env()
    structlog.reset_defaults()


# ---------------------------------------------------------------- redis ---


def test_redis_client_factories_do_not_connect() -> None:
    from core import redis_client

    sync = redis_client.sync_client("redis://127.0.0.1:6399/0")
    async_ = redis_client.async_client("redis://127.0.0.1:6399/0")
    assert sync.connection_pool.connection_kwargs["port"] == 6399
    assert async_.connection_pool.connection_kwargs["db"] == 0


def test_redis_lock_manager_from_url_uses_one_client_per_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fakeredis = pytest.importorskip("fakeredis")
    import core.redis_client as rc
    from core.locks import RedisLockManager, build_lock_manager

    server = fakeredis.FakeServer()
    created: list[Any] = []

    def factory(url: str) -> Any:
        client = fakeredis.FakeAsyncRedis(server=server)
        created.append(client)
        return client

    monkeypatch.setattr(rc, "async_client", factory)
    manager = build_lock_manager("redis", redis_url="redis://x:6379/0")
    assert isinstance(manager, RedisLockManager)

    async def use() -> None:
        async with manager.lock("a"):
            pass
        async with manager.lock("b"):
            pass

    asyncio.run(use())
    asyncio.run(use())
    assert len(created) == 2  # iki ayrı olay döngüsü → iki istemci
    with pytest.raises(ValueError):
        build_lock_manager("redis")
    with pytest.raises(ValueError):
        build_lock_manager("etcd")


# ---------------------------------------------------------------- money ---


def test_money_edge_cases() -> None:
    assert to_decimal(0.1) == Decimal("0.100000")
    assert to_decimal("12.3456789") == Decimal("12.345679")
    assert round_cents("2.345") == Decimal("2.35")
    assert to_float(None) == 0.0 and to_float(Decimal("1.2345"), 3) == 1.234
    for bad in ("abc", float("nan"), float("inf")):
        with pytest.raises(ValueError):
            to_decimal(bad)


# ---------------------------------------------------------------- runs / tx ---


def _register(client: TestClient, name: str) -> dict[str, Any]:
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "username": name,
            "password": "Parola!2345",
            "full_name": "Kapsam Test",
            "email": f"{name}@example.com",
        },
    )
    assert resp.status_code == 201, resp.text
    return dict(resp.json())


def test_runs_and_transactions_are_scoped_and_foreign_ids_are_404(client: TestClient) -> None:
    cid = create_customer(client, email="kapsam@example.com")["id"]
    pid = create_portfolio(client, cid, cash=300_000.0, holdings={})["id"]
    prop = client.post(f"/api/v1/advisor/rebalance/{pid}", params={"customer_id": cid}).json()
    client.post(f"/api/v1/proposals/{prop['proposal_id']}/approve")
    runs = client.get("/api/v1/runs", params={"customer_id": cid}).json()
    txs = client.get("/api/v1/transactions", params={"portfolio_id": pid}).json()
    assert runs and txs
    assert client.get(f"/api/v1/runs/{runs[0]['id']}").status_code == 200
    assert client.get(f"/api/v1/transactions/{txs[0]['id']}").status_code == 200

    other = auth_headers(_register(client, "kapsam.musteri")["access_token"])
    for path in (f"/api/v1/runs/{runs[0]['id']}", f"/api/v1/transactions/{txs[0]['id']}"):
        foreign = client.get(path, headers=other)
        missing = client.get(path.rsplit("/", 1)[0] + "/999999", headers=other)
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json()["detail"].replace(path.rsplit("/", 1)[1], "X") == missing.json()[
            "detail"
        ].replace("999999", "X")
    assert client.get("/api/v1/runs", headers=other).json() == []
    assert client.get("/api/v1/transactions", headers=other).json() == []


# ---------------------------------------------------------------- sources ---


def test_live_yahoo_source_derives_panel_and_chain_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.market_data.sources import ChainedSource, LiveYahooSource, SnapshotSource

    idx = pd.bdate_range("2026-01-01", periods=5)
    raw = pd.DataFrame(
        {"XU100.IS": 100.0, "USDTRY=X": 40.0, "GC=F": 3000.0, "THYAO.IS": 300.0, "XYZ.IS": 5.0},
        index=idx,
    )
    live = LiveYahooSource(macro_provider=SnapshotSource())
    monkeypatch.setattr(live, "_download_raw", lambda symbols: raw)
    panel = live.fetch_panel()
    assert panel["ALTIN_TL"].iloc[0] == pytest.approx(3000 * 40 / 31.1035)
    assert "TL_PPF" not in panel  # fonlar canlıda türetilmez

    async def run() -> tuple[dict[str, pd.DataFrame], str]:
        chain = ChainedSource(live, SnapshotSource(), mode="auto")
        frames = await chain.download_history(["XU100.IS", "TL_PPF", "XYZ.IS"])
        return frames, chain.last_source

    frames, source = asyncio.run(run())
    assert source == "mixed" and not frames["TL_PPF"].empty and not frames["XYZ.IS"].empty

    monkeypatch.setattr(live, "_download_raw", lambda symbols: pd.DataFrame())

    async def failing() -> None:
        live._cache = None
        await ChainedSource(live, SnapshotSource(), mode="live").download_history(["XU100.IS"])

    with pytest.raises(RuntimeError):
        asyncio.run(failing())


def test_snapshot_source_reads_legacy_csv_and_reports_missing(tmp_path: Path) -> None:
    from services.market_data.sources import SnapshotSource

    idx = pd.bdate_range("2026-01-01", periods=3)
    pd.DataFrame({"XU100.IS": [1.0, 2.0, 3.0]}, index=pd.Index(idx, name="date")).to_csv(
        tmp_path / "prices.csv.gz", compression="gzip"
    )
    (tmp_path / "macro_manual.csv").write_text("date,TUFE\n2026-01-31,100\n", encoding="utf-8")
    source = SnapshotSource(tmp_path)
    assert list(source.panel()["XU100.IS"]) == [1.0, 2.0, 3.0]
    assert source.meta() == {} and "TUFE" in source.macro()
    with pytest.raises(RuntimeError):
        SnapshotSource(tmp_path / "yok").panel()


def test_market_service_cache_invalidation_and_empty_frame() -> None:
    from services.market_service import MarketService
    from tests.conftest import FakeMarketSource

    svc = MarketService(FakeMarketSource(n_days=60))

    async def run() -> None:
        snaps = await svc.fetch_snapshots(["AAA", "BBB"])
        assert set(snaps) == {"AAA", "BBB"}
        svc.invalidate_cache(["AAA", "BBB"])
        svc.invalidate_cache()
        assert svc.build_returns_frame({}).empty

    asyncio.run(run())


# ---------------------------------------------------------------- anthropic ---


def test_anthropic_client_maps_messages_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from llm.clients import AnthropicClient, LLMConfigurationError, LLMProviderError

    with pytest.raises(LLMConfigurationError):
        AnthropicClient(api_key=" ", model="m")
    client = AnthropicClient(api_key="test-anthropic-0000", model="claude-test")
    seen: dict[str, Any] = {}

    async def create(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="Merhaba")],
            usage=SimpleNamespace(input_tokens=5, output_tokens=2),
        )

    monkeypatch.setattr(client._client.messages, "create", create)
    messages = [
        {"role": "system", "content": "sistem"},
        {"role": "user", "content": "soru"},
    ]
    resp = asyncio.run(client.chat(messages))
    assert resp.content == "Merhaba" and resp.prompt_tokens == 5
    assert seen["system"] == "sistem" and seen["messages"] == [{"role": "user", "content": "soru"}]
    with pytest.raises(LLMProviderError):
        asyncio.run(client.chat(messages, tools=[{"type": "function"}]))

    async def boom(**kwargs: Any) -> Any:
        raise RuntimeError("ağ yok")

    monkeypatch.setattr(client._client.messages, "create", boom)
    with pytest.raises(LLMProviderError):
        asyncio.run(client.chat(messages))
