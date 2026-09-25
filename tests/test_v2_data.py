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
    """Fake of TEFAS ``/api/funds/fonFiyatBilgiGetir`` (2026 API, JSON body)."""
    assert request.url.path.endswith("/api/funds/fonFiyatBilgiGetir")
    body = json.loads(request.content)
    assert body["periyod"] == 60 and body["dil"] == "TR"
    days = pd.bdate_range("2021-09-24", "2026-09-24")
    rows = [
        {"fonKodu": body["fonKodu"], "tarih": d.date().isoformat(), "fiyat": 1.0 + i * 0.001}
        for i, d in enumerate(days)
    ]
    rows.append(dict(rows[-1]))  # yinelenen satır tekilleştirilmeli
    return httpx.Response(200, json={"errorCode": None, "errorMessage": None, "resultList": rows})


def test_fetch_tefas_fund_with_fake_http() -> None:
    from scripts.fetch_real_data import fetch_tefas_fund

    transport = httpx.MockTransport(_tefas_handler)
    with httpx.Client(transport=transport) as client:
        series = fetch_tefas_fund("TST", date(2024, 1, 1), date(2024, 12, 31), client=client)
        full = fetch_tefas_fund("TST", client=client)
    assert isinstance(series, pd.Series)
    assert series.index.min() >= pd.Timestamp("2024-01-01")
    assert series.index.max() <= pd.Timestamp("2024-12-31")
    assert series.is_monotonic_increasing and series.index.is_unique
    assert len(series) > 200
    assert full.index.is_unique and full.index[0] == pd.Timestamp("2021-09-24")


def test_fetch_tefas_retries_after_rate_limit() -> None:
    from scripts.fetch_real_data import fetch_tefas_fund

    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, text="Too Many Requests")
        return _tefas_handler(request)

    waits: list[float] = []
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        series = fetch_tefas_fund("TST", client=client, sleep=waits.append)
    assert len(calls) == 2 and waits == [45.0] and not series.empty


def test_fetch_tcmb_cpi_pairs_rate_limit_and_unpublished_month() -> None:
    from scripts.fetch_real_data import fetch_tcmb_cpi

    index = {p: 100.0 + i for i, p in enumerate(pd.period_range("2025-12", "2026-08", freq="M"))}
    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if state["calls"] == 2:
            return httpx.Response(429, json={"error": "API Rate limit exceeded"})
        b = json.loads(request.content)
        start = pd.Period(f"{b['baslangicYil']}-{int(b['baslangicAy']):02d}", freq="M")
        end = pd.Period(f"{b['bitisYil']}-{int(b['bitisAy']):02d}", freq="M")
        if end not in index:
            return httpx.Response(400, json={"error": "yayımlanmadı"})
        fmt = lambda v: f"{v:,.5f}"  # noqa: E731 - TCMB binlik ayırıcı virgül kullanır
        return httpx.Response(
            200, json={"ilkYilTufe": fmt(index[start]), "sonYilTufe": fmt(index[end])}
        )

    waits: list[float] = []
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        cpi = fetch_tcmb_cpi(
            pd.Period("2025-12", freq="M"),
            pd.Period("2026-09", freq="M"),
            client=client,
            sleep=waits.append,
        )
    assert list(cpi.index) == [p.to_timestamp(how="end").normalize() for p in index]
    assert cpi.iloc[-1] == pytest.approx(108.0) and cpi.iloc[0] == pytest.approx(100.0)
    assert 30.0 in waits  # 429 sonrası bekleme


def test_parse_tcmb_policy_rate_table() -> None:
    from scripts.fetch_real_data import monthly_policy_rate, parse_tcmb_rate_table

    html = """<table><tr><td>Tarih</td><td>Borç Alma</td><td>Borç Verme</td></tr>
    <tr><td>20.05.2010</td><td>-</td><td>7.00</td></tr>
    <tr><td>12.12.2025</td><td>-</td><td>38,00</td></tr>
    <tr><td>23.01.2026</td><td>-</td><td>37.00</td></tr></table>"""
    steps = parse_tcmb_rate_table(html)
    assert steps.loc["2010-05-20"] == pytest.approx(0.07)
    assert steps.loc["2025-12-12"] == pytest.approx(0.38)
    months = pd.date_range("2025-11-30", periods=4, freq="ME")
    monthly = monthly_policy_rate(steps, months)
    assert list(monthly.round(4)) == [0.07, 0.38, 0.37, 0.37]


def test_splice_scales_proxy_to_real_start() -> None:
    from scripts.fetch_real_data import splice

    proxy = pd.Series(np.linspace(50, 100, 11), index=pd.bdate_range("2021-09-13", periods=11))
    real = pd.Series([200.0, 202.0, 204.0], index=pd.bdate_range("2021-09-24", periods=3))
    out, until = splice(real, proxy)
    assert until == "2021-09-23"
    assert out.loc["2021-09-24"] == 200.0 and out.index.is_monotonic_increasing
    # vekilin getirisi korunur, seviye gerçek seriye bağlanır
    assert out.loc["2021-09-23"] / out.loc["2021-09-22"] == pytest.approx(
        proxy.loc["2021-09-23"] / proxy.loc["2021-09-22"]
    )
    alone, none = splice(real, proxy[proxy.index >= "2021-09-24"])
    assert none is None and alone.equals(real)


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


def test_dns_lookups_are_blocked_too() -> None:
    import socket

    with pytest.raises(RuntimeError, match="ağ erişimi yasak"):
        socket.getaddrinfo("www.tefas.gov.tr", 443)


def test_effective_funding_rate_uses_llw_in_2017_2018() -> None:
    from scripts.fetch_real_data import effective_funding_rate, parse_tcmb_rate_table

    repo = pd.Series(
        {pd.Timestamp("2016-11-24"): 0.08, pd.Timestamp("2018-06-01"): 0.165},
        name="POLICY_RATE",
    )
    llw = parse_tcmb_rate_table(
        "<table><tr><td>Tarih</td><td>Borç Alma</td><td>Borç Verme</td></tr>"
        "<tr><td>24.11.16</td><td>7.25</td><td>8.50</td></tr>"
        "<tr><td>11.01.17</td><td>7.25</td><td>9.25</td></tr>"
        "<tr><td>16.03.17</td><td>7.25</td><td>12.75</td></tr></table>"
    )
    eff = effective_funding_rate(repo, llw)
    at = lambda d: float(eff[eff.index <= pd.Timestamp(d)].iloc[-1])  # noqa: E731
    assert at("2016-12-30") == pytest.approx(0.08)  # pencere öncesi: repo
    assert at("2017-02-01") == pytest.approx(0.0925)  # pencere içi: GLP borç verme
    assert at("2018-05-31") == pytest.approx(0.1275)
    assert at("2018-06-01") == pytest.approx(0.165)  # repo yeniden politika faizi


def test_quality_detects_forward_filled_hole() -> None:
    from services.market_data.quality import assess_series

    idx = pd.bdate_range("2024-01-01", periods=200)
    rng = np.random.default_rng(1)
    values = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 200))), index=idx)
    values.iloc[100:112] = values.iloc[99]  # 12 gün ileri doldurulmuş boşluk
    kinds = {i["kind"] for i in assess_series("X", values, as_of=idx[-1])["issues"]}
    assert "gap" in kinds
