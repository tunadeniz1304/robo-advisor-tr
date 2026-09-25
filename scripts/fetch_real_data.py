"""Gerçek piyasa ve makro verisini indirip offline snapshot üretir.

Kullanım::

    python scripts/fetch_real_data.py                 # tüm seriler (~10-15 dk; TÜFE yavaş)
    python scripts/fetch_real_data.py --reuse-cpi     # TÜFE'yi mevcut snapshot'tan al

Kaynaklar (ayrıntı ve lisans notları: ``docs/DATA.md``):

* **Yahoo Finance** (``yfinance``): BIST 100/30, BIST hisseleri, USDTRY, EURTRY,
  ons altın (GC=F) ve eurobond vekili için EMB — 10 yıl, düzeltilmiş kapanış.
* **TEFAS** (``/api/funds/fonFiyatBilgiGetir``): fonların günlük birim pay
  fiyatı. TEFAS en fazla 5 yıl verir; sınıf temsilcilerinin daha eski kısmı
  belgelenmiş bir vekille eklenir ve ``proxy_until`` olarak işaretlenir.
* **TCMB**: TÜFE (2003=100) TCMB enflasyon hesaplayıcı API'sinden, politika
  faizi (1 hafta repo) TCMB faiz tablosundan. ``EVDS_API_KEY`` varsa TÜFE ve
  AOFM EVDS3'ten alınır. Hiçbiri yoksa son çare ``macro_manual.csv``
  (``manual_approx``, arayüzde "yaklaşık" rozeti).

Testler ağa çıkmaz: HTTP çağrıları ``httpx.Client`` parametresiyle yapılır ve
testlerde ``httpx.MockTransport`` ile taklit edilir.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # TextIO lacks it; real streams have it
    except AttributeError:  # pragma: no cover
        pass

import httpx  # noqa: E402
import pandas as pd  # noqa: E402

from services.market_data.derive import TROY_OUNCE_GRAMS, proxy_series  # noqa: E402
from services.market_data.sources import (  # noqa: E402
    MACRO_FILE,
    MACRO_MANUAL_FILE,
    META_FILE,
    PRICES_FILE,
    SNAPSHOT_DIR,
    read_macro_csv,
)
from services.market_data.universe import UNDERLYING_YAHOO, UNIVERSE  # noqa: E402

TEFAS_PRICE_URL = "https://www.tefas.gov.tr/api/funds/fonFiyatBilgiGetir"
TEFAS_MAX_PERIOD_MONTHS = 60
TCMB_CPI_URL = "https://appg.tcmb.gov.tr/KIMENFH/enflasyon/hesapla"
TCMB_REPO_URL = (
    "https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB+TR/Main+Menu/Temel+Faaliyetler/"
    "Para+Politikasi/Merkez+Bankasi+Faiz+Oranlari/1+Hafta+Repo"
)
TCMB_LLW_URL = (
    "https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB+TR/Main+Menu/Temel+Faaliyetler/"
    "Para+Politikasi/Merkez+Bankasi+Faiz+Oranlari/Gec+Likidite+Penceresi+%28LON%29"
)
# Ocak 2017 – Mayıs 2018: TCMB bir hafta repo ile fonlamayı fiilen durdurup piyasayı geç likidite
# penceresinden (GLP) fonladı; bu dönemde etkin fonlama faizi GLP borç verme oranıdır. 1 Haziran
# 2018'de bir hafta repo yeniden politika faizi oldu.
LLW_FUNDING_WINDOW = ("2017-01-01", "2018-06-01")
EVDS3_URL = "https://evds3.tcmb.gov.tr/igmevdsms-dis"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
MACRO_START = pd.Period("2015-12", freq="M")


@dataclass(frozen=True)
class SeriesResult:
    """One instrument series and its provenance."""

    symbol: str
    series: pd.Series
    source: str
    is_proxy: bool
    proxy_until: str | None = None
    note: str | None = None


# ---------------------------------------------------------------- HTTP ---

TRANSIENT_STATUS = frozenset({403, 429, 500, 502, 503, 504})


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    retries: int = 6,
    backoff: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
    ok_status: frozenset[int] = frozenset(),
    **kwargs: Any,
) -> httpx.Response:
    """HTTP request with retries on connection errors and transient statuses.

    TCMB and TEFAS answer bursts with 429 (sometimes 403/5xx) and the
    machine running the script may see intermittent DNS failures. Statuses in
    ``ok_status`` (e.g. 400 = "not published yet") are returned to the caller.
    """
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            last = exc
            sleep(min(backoff, 5.0 * (attempt + 1)))
            continue
        if resp.status_code in ok_status:
            return resp
        if resp.status_code in TRANSIENT_STATUS:
            last = httpx.HTTPStatusError(
                f"HTTP {resp.status_code}", request=resp.request, response=resp
            )
            sleep(backoff)
            continue
        resp.raise_for_status()
        return resp
    raise RuntimeError(f"{url} alınamadı ({retries} deneme)") from last


# ---------------------------------------------------------------- TEFAS ---


def fetch_tefas_fund(
    code: str,
    start: date | None = None,
    end: date | None = None,
    *,
    client: httpx.Client,
    retries: int = 3,
    backoff: float = 45.0,
    sleep: Callable[[float], None] = time.sleep,
) -> pd.Series:
    """Daily NAV of a TEFAS fund (last five years at most).

    TEFAS answers one request with the whole ``periyod`` window (60 months is
    the maximum) and HTTP 429 to bursts; the block clears in ~45 s.
    """
    payload = {"fonKodu": code, "dil": "TR", "periyod": TEFAS_MAX_PERIOD_MONTHS}
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    resp = request_with_retry(
        client,
        "POST",
        TEFAS_PRICE_URL,
        retries=retries,
        backoff=backoff,
        sleep=sleep,
        json=payload,
        headers=headers,
        timeout=60,
    )
    body = resp.json()
    rows = body.get("resultList") or []
    if body.get("errorCode") not in (None, "", 0, "0") and not rows:
        raise RuntimeError(f"TEFAS hata: {body.get('errorMessage')}")
    data = {
        pd.Timestamp(str(r["tarih"])[:10]): float(r["fiyat"])
        for r in rows
        if r.get("tarih") and r.get("fiyat") not in (None, "")
    }
    series = pd.Series(data, dtype=float).sort_index()
    series = series[~series.index.duplicated(keep="last")]
    series = series[series > 0]
    if start is not None:
        series = series[series.index >= pd.Timestamp(start)]
    if end is not None:
        series = series[series.index <= pd.Timestamp(end)]
    series.name = code
    return series


# ---------------------------------------------------------------- TCMB ---


def _cpi_value(text: str) -> float:
    return float(str(text).replace(",", ""))


def fetch_tcmb_cpi(
    start: pd.Period,
    end: pd.Period,
    *,
    client: httpx.Client,
    pause: float = 3.0,
    backoff: float = 30.0,
    retries: int = 8,
    sleep: Callable[[float], None] = time.sleep,
) -> pd.Series:
    """Monthly TÜFE (2003=100) from the TCMB inflation calculator API.

    Each call returns the index of its start and end month, so consecutive
    month pairs are requested. The API rate-limits bursts (HTTP 429) and
    answers 400 for months not yet published — the series stops there.
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Origin": "https://herkesicin.tcmb.gov.tr",
        "Referer": "https://herkesicin.tcmb.gov.tr/",
        "User-Agent": USER_AGENT,
    }

    recent = pd.Timestamp.today().to_period("M") - 2

    def query(a: pd.Period, b: pd.Period) -> tuple[float, float] | None:
        body = {
            "baslangicYil": str(a.year),
            "baslangicAy": str(a.month),
            "bitisYil": str(b.year),
            "bitisAy": str(b.month),
            "malSepeti": "100",
        }
        for _ in range(retries):
            resp = request_with_retry(
                client,
                "POST",
                TCMB_CPI_URL,
                retries=retries,
                backoff=backoff,
                sleep=sleep,
                ok_status=frozenset({400, 500}),
                json=body,
                headers=headers,
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                return _cpi_value(data["ilkYilTufe"]), _cpi_value(data["sonYilTufe"])
            if b >= recent:
                return None  # henüz yayımlanmamış ay (API 400 veya 500 döner)
            sleep(backoff)  # eski ay için 500: geçici hata, tekrar dene
        raise RuntimeError(f"TÜFE alınamadı: {a}–{b}")

    months = list(pd.period_range(start, end, freq="M"))
    values: dict[pd.Period, float] = {}
    i = 0
    while i < len(months) - 1:
        a, b = months[i], months[i + 1]
        pair = query(a, b)
        if pair is None:
            if a not in values and i > 0:  # b yayımlanmamış: a'yı önceki ayla birlikte al
                single = query(months[i - 1], a)
                if single is not None:
                    values[a] = single[1]
            break
        values[a], values[b] = pair
        i += 2
        if i < len(months):
            if i == len(months) - 1:  # tek kalan son ay
                last = query(months[i - 1], months[i])
                if last is not None:
                    values[months[i]] = last[1]
                break
            sleep(pause)
    if not values:
        raise RuntimeError("TÜFE serisi boş.")
    return pd.Series(
        {p.to_timestamp(how="end").normalize(): v for p, v in sorted(values.items())},
        dtype=float,
        name="TUFE",
    )


def _tr_date(text: str) -> pd.Timestamp:
    raw = str(text).strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            return pd.Timestamp(datetime.strptime(raw, fmt))
        except ValueError:
            continue
    raise ValueError(f"Tarih çözümlenemedi: {raw}")


def _tr_number(text: Any) -> float | None:
    raw = str(text).strip()
    if raw in {"", "-", "nan", "None"}:
        return None
    return float(raw.replace(".", "").replace(",", ".")) if "," in raw else float(raw)


def parse_tcmb_rate_table(html: str) -> pd.Series:
    """Parse a TCMB rate page (``Tarih | Borç Alma | Borç Verme``) into a step series."""
    tables = pd.read_html(io.StringIO(html), thousands=None)
    if not tables:
        raise ValueError("TCMB faiz tablosu bulunamadı.")
    table = tables[0]
    rows: dict[pd.Timestamp, float] = {}
    for _, row in table.iloc[1:].iterrows():
        try:
            when = _tr_date(row.iloc[0])
        except ValueError:
            continue
        lend = _tr_number(row.iloc[2]) if row.shape[0] > 2 else None
        borrow = _tr_number(row.iloc[1])
        value = lend if lend is not None else borrow
        if value is not None:
            rows[when] = value / 100.0
    if not rows:
        raise ValueError("TCMB faiz tablosunda satır yok.")
    return pd.Series(rows, dtype=float, name="POLICY_RATE").sort_index()


def fetch_rate_table(
    url: str, *, client: httpx.Client, sleep: Callable[[float], None] = time.sleep
) -> pd.Series:
    """A TCMB rate table (decision dates, fraction)."""
    resp = request_with_retry(
        client,
        "GET",
        url,
        sleep=sleep,
        headers={"User-Agent": USER_AGENT},
        timeout=30,
        follow_redirects=True,
    )
    return parse_tcmb_rate_table(resp.text)


def fetch_policy_rate(
    *, client: httpx.Client, sleep: Callable[[float], None] = time.sleep
) -> pd.Series:
    """TCMB one-week repo rate (decision dates, fraction)."""
    return fetch_rate_table(TCMB_REPO_URL, client=client, sleep=sleep)


def effective_funding_rate(
    repo: pd.Series,
    llw: pd.Series,
    window: tuple[str, str] = LLW_FUNDING_WINDOW,
) -> pd.Series:
    """One-week repo, except the LLW lending rate while TCMB funded through it."""
    start, end = pd.Timestamp(window[0]), pd.Timestamp(window[1])
    grid = repo.index.union(llw.index).union(pd.DatetimeIndex([start, end]))
    out = repo.reindex(grid).ffill()
    late = llw.reindex(grid).ffill()
    mask = (grid >= start) & (grid < end)
    out[mask] = late[mask]
    return out.dropna().rename("POLICY_RATE")


def fetch_evds(
    key: str, start: date, end: date, *, client: httpx.Client
) -> pd.DataFrame:  # pragma: no cover - anahtar gerektirir
    """TÜFE and AOFM from EVDS3 (key in the ``key`` header, never logged)."""
    codes = {"TUFE": "TP.FG.J0", "POLICY_RATE": "TP.APIFON4"}
    path = (
        f"/series={'-'.join(codes.values())}&startDate={start:%d-%m-%Y}&endDate={end:%d-%m-%Y}"
        "&type=json&frequency=5&aggregationTypes=avg-avg"
    )
    resp = client.get(EVDS3_URL + path, headers={"key": key}, timeout=30)
    resp.raise_for_status()
    rows = []
    for item in resp.json().get("items", []):
        row: dict[str, Any] = {"date": pd.Period(item["Tarih"], freq="M").to_timestamp("M")}
        for name, code in codes.items():
            value = item.get(code.replace(".", "_"))
            row[name] = float(value) if value not in (None, "") else None
        rows.append(row)
    frame = pd.DataFrame(rows).set_index("date").sort_index()
    frame["POLICY_RATE"] = frame["POLICY_RATE"] / 100.0
    return frame


# ---------------------------------------------------------------- Yahoo ---


def fetch_yahoo(symbols: list[str], period: str = "10y") -> pd.DataFrame:  # pragma: no cover
    """Adjusted daily closes from Yahoo Finance (network)."""
    import yfinance as yf

    closes: dict[str, pd.Series] = {}
    for sym in symbols:
        hist = yf.Ticker(sym).history(period=period, auto_adjust=True)
        if hist is not None and not hist.empty:
            series = hist["Close"].dropna()
            series.index = pd.DatetimeIndex(series.index).tz_localize(None).normalize()
            closes[sym] = series[~series.index.duplicated(keep="last")]
        time.sleep(0.3)
    return pd.DataFrame(closes).sort_index()


# ---------------------------------------------------------------- build ---


def splice(real: pd.Series, proxy: pd.Series) -> tuple[pd.Series, str | None]:
    """Extend ``real`` backwards with ``proxy`` scaled to meet it on day one.

    Returns the spliced series and the last proxy date (``None`` if the real
    series already covers the proxy's range).
    """
    real = real.dropna().sort_index()
    proxy = proxy.dropna().sort_index()
    first = real.index[0]
    before = proxy[proxy.index < first]
    anchor = proxy[proxy.index <= first]
    if before.empty or anchor.empty:
        return real, None
    scaled = before * (float(real.iloc[0]) / float(anchor.iloc[-1]))
    out = pd.concat([scaled, real]).sort_index()
    return out[~out.index.duplicated(keep="last")], before.index[-1].date().isoformat()


def monthly_policy_rate(steps: pd.Series, months: pd.DatetimeIndex) -> pd.Series:
    """Month-end value of a step (decision-date) rate series."""
    grid = steps.index.union(months)
    return steps.reindex(grid).ffill().bfill().reindex(months)


def build_instruments(
    raw: pd.DataFrame,
    funds: dict[str, pd.Series],
    macro: pd.DataFrame,
) -> list[SeriesResult]:
    """Instrument series with provenance from raw Yahoo data and TEFAS NAVs."""
    frame = raw.sort_index().ffill()
    # Vekiller faizi karar tarihinde değiştirir; ay sonu değeri şoku haftalarca geciktirir.
    steps = macro.attrs.get("policy_steps")
    rate_source = pd.DataFrame({"POLICY_RATE": steps}) if steps is not None else macro
    results: list[SeriesResult] = []
    for spec in UNIVERSE:
        if spec.source == "yfinance" and spec.yahoo_symbol in frame.columns:
            results.append(
                SeriesResult(
                    spec.symbol, frame[spec.yahoo_symbol], f"yahoo:{spec.yahoo_symbol}", False
                )
            )
        elif spec.source == "derived" and {"GC=F", "USDTRY=X"} <= set(frame.columns):
            gram = frame["GC=F"] * frame["USDTRY=X"] / TROY_OUNCE_GRAMS
            results.append(
                SeriesResult(
                    spec.symbol,
                    gram,
                    "yahoo:GC=F×USDTRY=X/31.1035",
                    False,
                    note="Birim dönüşümü (ons → gram, USD → TL); iç piyasa primi içermez.",
                )
            )
        elif spec.source == "tefas" and spec.symbol in funds:
            real = funds[spec.symbol]
            if spec.splice:
                proxy = proxy_series(spec.splice, spec.symbol, frame, rate_source)
                series, until = splice(real, proxy)
                results.append(
                    SeriesResult(
                        spec.symbol,
                        series,
                        f"tefas:{spec.tefas_code}",
                        False,
                        proxy_until=until,
                        note=f"{until} ve öncesi vekil ({spec.splice}); sonrası gerçek TEFAS fiyatı."
                        if until
                        else None,
                    )
                )
            else:
                results.append(SeriesResult(spec.symbol, real, f"tefas:{spec.tefas_code}", False))
        elif spec.source == "tefas" and spec.splice:
            # TEFAS erişilemedi: tamamen vekil — açıkça işaretlenir.
            proxy = proxy_series(spec.splice, spec.symbol, frame, rate_source)
            results.append(
                SeriesResult(
                    spec.symbol,
                    proxy,
                    f"proxy:{spec.splice}",
                    True,
                    note="TEFAS verisi alınamadı; tamamı vekil seri.",
                )
            )
    return results


def write_snapshot(
    results: list[SeriesResult],
    macro: pd.DataFrame,
    macro_sources: dict[str, str],
    out_dir: Path,
    fetched_at: datetime | None = None,
) -> dict[str, Any]:
    """Write ``prices.parquet``, ``macro.csv`` and ``meta.json``; return the meta."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = (fetched_at or datetime.now(UTC)).isoformat(timespec="seconds")
    panel = pd.DataFrame({r.symbol: r.series for r in results}).sort_index()
    panel = panel.dropna(how="all")
    # Tüm seriler için ortak son gün (ör. Yahoo'nun yarım günlük barı fonlarda boş kalır).
    common_end = min(r.series.dropna().index.max() for r in results if not r.series.dropna().empty)
    panel = panel.loc[:common_end].ffill()  # tatil/kapalı günlerde son fiyat taşınır
    panel.index = pd.DatetimeIndex(panel.index, name="date")
    panel.round(6).astype("float64").to_parquet(out_dir / PRICES_FILE, compression="zstd")
    legacy = out_dir / "prices.csv.gz"
    if legacy.exists():
        legacy.unlink()
    macro_out = macro.copy()
    macro_out.index.name = "date"
    with (out_dir / MACRO_FILE).open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(
            "# Kaynaklar: "
            + ", ".join(f"{k}={v}" for k, v in macro_sources.items())
            + ". TÜFE 2003=100, POLICY_RATE kesir (ay sonu), USDTRY ay sonu.\n"
        )
        macro_out.round(6).to_csv(fh)
    series_meta: dict[str, Any] = {}
    for r in results:
        s = r.series.dropna()
        series_meta[r.symbol] = {
            "source": r.source,
            "fetched_at": stamp,
            "first_date": s.index.min().date().isoformat() if not s.empty else None,
            "last_date": s.index.max().date().isoformat() if not s.empty else None,
            "rows": int(s.shape[0]),
            "is_proxy": bool(r.is_proxy),
            "has_proxy_segment": bool(r.is_proxy or r.proxy_until),
            "proxy_until": r.proxy_until,
            "note": r.note,
        }
    macro_meta = {
        col: {
            "source": macro_sources.get(col, "unknown"),
            "fetched_at": stamp,
            "first_date": macro[col].dropna().index.min().date().isoformat(),
            "last_date": macro[col].dropna().index.max().date().isoformat(),
            "rows": int(macro[col].dropna().shape[0]),
            "is_proxy": macro_sources.get(col) == "manual_approx",
        }
        for col in macro.columns
        if not macro[col].dropna().empty
    }
    meta = {
        "generated_at": stamp,
        "start": panel.index.min().date().isoformat(),
        "end": panel.index.max().date().isoformat(),
        "rows": int(panel.shape[0]),
        "instruments": list(panel.columns),
        "price_source": "yahoo_finance+tefas",
        "macro_source": "+".join(sorted(set(macro_sources.values()))),
        "series": series_meta,
        "macro": macro_meta,
        "note": "Bilgi amaçlıdır. proxy_until tarihine kadar olan kısım belgelenmiş vekildir.",
    }
    (out_dir / META_FILE).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return meta


# ---------------------------------------------------------------- main ---


def _macro(
    args: argparse.Namespace, client: httpx.Client, out: Path, usdtry: pd.Series
) -> tuple[pd.DataFrame, dict[str, str]]:
    import os

    end = pd.Timestamp.today().to_period("M")
    months = pd.date_range(MACRO_START.to_timestamp(), end.to_timestamp(how="end"), freq="ME")
    sources: dict[str, str] = {}
    key = os.getenv("EVDS_API_KEY")
    if key:
        try:  # pragma: no cover - anahtar gerektirir
            evds = fetch_evds(key, MACRO_START.to_timestamp().date(), date.today(), client=client)
            frame = evds.reindex(months)
            frame["USDTRY"] = usdtry.resample("ME").last().reindex(months)
            return frame, {
                "TUFE": "evds3:TP.FG.J0",
                "POLICY_RATE": "evds3:TP.APIFON4",
                "USDTRY": "yahoo",
            }
        except Exception as exc:  # noqa: BLE001
            print(f"EVDS alınamadı ({type(exc).__name__}); anahtarsız kaynaklara geçiliyor.")
    frame = pd.DataFrame(index=months)
    policy_steps: pd.Series | None = None
    try:
        steps = fetch_policy_rate(client=client)
        sources["POLICY_RATE"] = "tcmb:1_hafta_repo"
        try:
            llw = fetch_rate_table(TCMB_LLW_URL, client=client)
            steps = effective_funding_rate(steps, llw)
            sources["POLICY_RATE"] = "tcmb:1_hafta_repo+glp(2017-01..2018-05)"
        except Exception as exc:  # noqa: BLE001
            print(f"GLP faizi alınamadı ({type(exc).__name__}); yalnız repo kullanılıyor.")
        frame["POLICY_RATE"] = monthly_policy_rate(steps, months)
        policy_steps = steps
        print(
            f"Politika faizi: {len(steps)} karar, son {steps.index[-1].date()} = {steps.iloc[-1]:.2%}"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Politika faizi alınamadı ({type(exc).__name__}).")
    previous = out / MACRO_FILE
    if args.reuse_cpi and previous.is_file():
        old = read_macro_csv(previous)
        if "TUFE" in old.columns:
            frame["TUFE"] = old["TUFE"].reindex(months)
            sources["TUFE"] = "tcmb:enflasyon_hesaplayici(önbellek)"
    if "TUFE" not in frame.columns:
        try:
            cpi = fetch_tcmb_cpi(MACRO_START, end, client=client, pause=args.cpi_pause)
            frame["TUFE"] = cpi.reindex(months)
            sources["TUFE"] = "tcmb:enflasyon_hesaplayici"
            print(f"TÜFE: {cpi.shape[0]} ay, son {cpi.index[-1].date()} = {cpi.iloc[-1]:.2f}")
        except Exception as exc:  # noqa: BLE001
            print(f"TÜFE alınamadı ({type(exc).__name__}).")
    manual = read_macro_csv(out / MACRO_MANUAL_FILE)
    for col in ("TUFE", "POLICY_RATE"):
        if col not in frame.columns or frame[col].dropna().empty:
            frame[col] = manual[col].reindex(months)
            sources[col] = "manual_approx"
    frame["USDTRY"] = usdtry.resample("ME").last().reindex(months)
    sources["USDTRY"] = "yahoo:USDTRY=X"
    frame["POLICY_RATE"] = frame["POLICY_RATE"].ffill()
    result = frame[["TUFE", "POLICY_RATE", "USDTRY"]].dropna(how="all")
    if policy_steps is not None:
        result.attrs["policy_steps"] = policy_steps
    return result, sources


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--period", default="10y")
    parser.add_argument("--out", default=str(SNAPSHOT_DIR))
    parser.add_argument("--reuse-cpi", action="store_true", help="TÜFE'yi mevcut snapshot'tan al")
    parser.add_argument("--cpi-pause", type=float, default=3.0)
    args = parser.parse_args()
    out = Path(args.out)

    yahoo = sorted(set(UNDERLYING_YAHOO) | {"USDTRY=X", "GC=F", "EMB"})
    print(f"Yahoo Finance: {len(yahoo)} sembol indiriliyor…")
    raw = fetch_yahoo(yahoo, args.period)
    if raw.empty or "XU100.IS" not in raw.columns:
        print("Yahoo verisi alınamadı; snapshot değiştirilmedi.")
        return 1
    raw = raw[raw.index >= raw["XU100.IS"].dropna().index.min()]

    funds: dict[str, pd.Series] = {}
    with httpx.Client() as client:
        for spec in UNIVERSE:
            if not spec.tefas_code:
                continue
            try:
                funds[spec.symbol] = fetch_tefas_fund(spec.tefas_code, client=client)
                s = funds[spec.symbol]
                print(
                    f"TEFAS {spec.tefas_code}: {len(s)} gün ({s.index[0].date()} → {s.index[-1].date()})"
                )
            except Exception as exc:  # noqa: BLE001
                print(f"TEFAS {spec.tefas_code} alınamadı ({type(exc).__name__}).")
            time.sleep(0.6)
        macro, sources = _macro(args, client, out, raw["USDTRY=X"])

    results = build_instruments(raw, funds, macro)
    meta = write_snapshot(results, macro, sources, out)
    proxies = [k for k, v in meta["series"].items() if v["is_proxy"]]
    spliced = {k: v["proxy_until"] for k, v in meta["series"].items() if v["proxy_until"]}
    size = sum(p.stat().st_size for p in out.iterdir() if p.is_file())
    print(
        f"Snapshot: {meta['rows']} gün × {len(meta['instruments'])} enstrüman "
        f"({meta['start']} → {meta['end']}), {size / 1024:.0f} KB. Makro: {sources}. "
        f"Tam vekil: {proxies or 'yok'}. Eklenmiş vekil dönemleri: {spliced}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
