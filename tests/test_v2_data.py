"""v2 audit findings B.9–B.11 and D.21: real data, quality layer, badges.

* B.9  — ``scripts/fetch_real_data.py`` downloads real series (tested with a
  fake HTTP transport; the test suite never touches the network).
* B.10 — data-quality checks (gaps, z-score jumps, stale data, split-like
  jumps) served at ``GET /api/v1/data/quality``.
* B.11 — the snapshot records per-series metadata (``source``,
  ``fetched_at``, ``first_date``, ``last_date``, ``rows``, ``is_proxy``) and
  stays small.
* D.21 — the status endpoint reflects real / snapshot / approximate data and
  live / demo AI.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

SNAPSHOT = Path("data/snapshots")
SERIES_KEYS = {"source", "fetched_at", "first_date", "last_date", "rows", "is_proxy"}


def test_snapshot_meta_has_per_series_metadata() -> None:
    meta = json.loads((SNAPSHOT / "meta.json").read_text(encoding="utf-8"))
    series = meta["series"]
    assert series, "seri metadata'sı yok"
    for name, info in series.items():
        assert SERIES_KEYS <= set(info), name
        assert isinstance(info["is_proxy"], bool)
    # Para piyasası, tahvil, eurobond ve altın artık gerçek fon/piyasa verisi.
    for sym in ("TL_PPF", "TL_TAHVIL", "EUROBOND_TL", "ALTIN_TL"):
        assert series[sym]["is_proxy"] is False, sym
    assert meta["macro"]["TUFE"]["source"] != "manual_approx"


def test_snapshot_is_small() -> None:
    total = sum(p.stat().st_size for p in SNAPSHOT.iterdir() if p.is_file())
    assert total < 5 * 1024 * 1024


def _tefas_handler(request: httpx.Request) -> httpx.Response:
    body = dict(x.split("=", 1) for x in request.content.decode().split("&"))
    start = pd.Timestamp(pd.to_datetime(body["bastarih"], dayfirst=True))
    end = pd.Timestamp(pd.to_datetime(body["bittarih"], dayfirst=True))
    days = pd.bdate_range(start, end)
    rows = [
        {
            "TARIH": str(int(d.timestamp() * 1000)),
            "FONKODU": body["fonkod"],
            "FIYAT": 1.0 + i * 0.001,
        }
        for i, d in enumerate(days)
    ]
    return httpx.Response(200, json={"data": rows})


def test_fetch_tefas_fund_with_fake_http() -> None:
    from scripts.fetch_real_data import fetch_tefas_fund

    transport = httpx.MockTransport(_tefas_handler)
    with httpx.Client(transport=transport) as client:
        series = fetch_tefas_fund("TST", date(2024, 1, 1), date(2024, 12, 31), client=client)
    assert isinstance(series, pd.Series)
    assert series.index.min() >= pd.Timestamp("2024-01-01")
    assert series.index.max() <= pd.Timestamp("2024-12-31")
    assert series.is_monotonic_increasing and series.index.is_unique
    assert len(series) > 200  # parçalı (chunked) indirme birleştirildi


def test_build_snapshot_marks_proxies_and_sources(tmp_path: Path) -> None:
    from scripts.fetch_real_data import SeriesResult, write_snapshot

    idx = pd.bdate_range("2020-01-01", periods=400)
    real = SeriesResult("TL_PPF", pd.Series(np.linspace(1, 2, 400), index=idx), "tefas:TST", False)
    proxy = SeriesResult(
        "EUROBOND_TL", pd.Series(np.linspace(1, 3, 400), index=idx), "proxy:derive", True
    )
    macro = pd.DataFrame(
        {"TUFE": np.linspace(100, 200, 20), "POLICY_RATE": 0.4},
        index=pd.date_range("2020-01-31", periods=20, freq="ME"),
    )
    meta = write_snapshot([real, proxy], macro, {"TUFE": "tuik", "POLICY_RATE": "tcmb"}, tmp_path)
    assert meta["series"]["TL_PPF"]["is_proxy"] is False
    assert meta["series"]["EUROBOND_TL"]["is_proxy"] is True
    assert meta["series"]["TL_PPF"]["source"] == "tefas:TST"
    assert (tmp_path / "meta.json").is_file()


def test_quality_checks_detect_gaps_jumps_and_staleness() -> None:
    from services.market_data.quality import assess_series

    idx = pd.bdate_range("2024-01-01", periods=300)
    values = pd.Series(100 * np.exp(np.cumsum(np.full(300, 0.0005))), index=idx)
    values.iloc[150] *= 1.6  # sıçrama
    values = values.drop(values.index[200:215])  # 15 işlem günü boşluk
    report = assess_series("TEST", values, as_of=pd.Timestamp("2025-06-30"))
    kinds = {issue["kind"] for issue in report["issues"]}
    assert {"jump", "gap", "stale"} <= kinds
    assert report["status"] in {"warning", "error"}


def test_quality_clean_series_is_ok() -> None:
    from services.market_data.quality import assess_series

    idx = pd.bdate_range("2024-01-01", periods=300)
    rng = np.random.default_rng(0)
    values = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 300))), index=idx)
    report = assess_series("TEST", values, as_of=idx[-1])
    assert report["status"] == "ok" and report["issues"] == []


def test_quality_endpoint(client: TestClient) -> None:
    resp = client.get("/api/v1/data/quality")
    assert resp.status_code == 200
    body = resp.json()
    assert {"status", "series", "checked_at"} <= set(body)
    assert all("is_proxy" in s for s in body["series"])


def test_status_badges_reflect_reality(client: TestClient) -> None:
    body = client.get("/api/v1/system/status").json()
    assert body["data"]["mode"] in {"gercek", "snapshot", "yaklasik"}
    assert isinstance(body["data"]["proxy_series"], list)
    assert body["quality"]["status"] in {"ok", "warning", "error"}
    assert body["ai"]["mode"] in {"live", "demo"}


def test_no_network_in_tests() -> None:
    with pytest.raises(RuntimeError, match="ağ erişimi yasak"):
        httpx.get("https://www.tefas.gov.tr", timeout=1)
