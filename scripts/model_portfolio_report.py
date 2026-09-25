"""10 model portföyün walk-forward performansını hesaplar ve raporlar.

Kullanım::

    python scripts/model_portfolio_report.py      # data/snapshots + docs/MODEL_PORTFOLIOS.md

Çıktılar: ``data/snapshots/model_performance.json`` (API bunu sunar) ve
``docs/MODEL_PORTFOLIOS.md`` (seviye karşılaştırma tablosu).
"""

from __future__ import annotations

import asyncio
import json
import sys
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

import structlog  # noqa: E402

structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(40))

from core.policy import get_policy  # noqa: E402
from services.analytics.model_tracking import model_portfolio_performance  # noqa: E402
from services.market_data.service import MarketDataService  # noqa: E402
from services.market_data.sources import SNAPSHOT_DIR, SnapshotSource  # noqa: E402

OUT_JSON = SNAPSHOT_DIR / "model_performance.json"
OUT_DOC = ROOT / "docs" / "MODEL_PORTFOLIOS.md"


def pct(x: Any) -> str:
    return "—" if x is None else f"%{float(x) * 100:.1f}".replace(".", ",")


def num(x: Any) -> str:
    return "—" if x is None else f"{float(x):.2f}".replace(".", ",")


def render(result: dict[str, Any]) -> str:
    policy = get_policy()
    first = next(iter(result["levels"].values()))
    target = result["benchmarks"].get("TUFE+3", {}).get("cagr") or 0.0
    xu = result["benchmarks"].get("XU100", {}).get("cagr") or 0.0
    wf_cagr = [row["walk_forward"]["cagr"] or 0.0 for row in result["levels"].values()]
    beat = sum(c > target for c in wf_cagr)
    beat_xu = sum(c > xu for c in wf_cagr)
    lines = [
        "# Model portföyler: seviye karşılaştırması (walk-forward)",
        "",
        "Bu dosya `scripts/model_portfolio_report.py` ile gerçek veri snapshot'ından üretilir "
        f"(snapshot sonu: {result['snapshot_end']}).",
        "",
        f"* **Test dönemi:** {first['test_start']} → {first['test_end']} (her seviye için aynı).",
        f"* **Yöntem:** `{result['method']}` optimizer, {policy.backtest['window_days']} işlem "
        f"günlük tahmin penceresi, {policy.backtest['rebalance_days']} günde bir yeniden "
        "optimizasyon; her tarihte yalnızca o tarihe kadarki veri kullanılır (look-ahead yok).",
        "* **Statik model:** model sınıf ağırlıkları sınıf sepetlerine eşit dağıtılır (hisse "
        "sınıfı = evrendeki 15 BIST hissesi, döviz = USD+EUR, fonlar = sınıf temsilcisi), bant "
        "politikasıyla rebalance. Hisse sepeti bugünün büyük şirketlerinden oluştuğu için "
        "**hayatta kalma yanlılığı** taşır; statik sonuçlar bu yüzden iyimserdir. Walk-forward "
        "de aynı hisse evreninden seçim yaptığı için bu yanlılığı taşır.",
        "* **Walk-forward evreni:** tüm dönem boyunca verisi eksiksiz olan semboller; ilk "
        "yeniden optimizasyonların tahmin pencereleri 2021-09 öncesi vekil fon verisi içerir "
        "(bkz. DATA).",
        "* **Sharpe** risksiz getiri olarak dönemin TL politika faizlerinin günlük bileşik "
        f"karşılığını kullanır (%{result['risk_free_rate'] * 100:.1f}); işlem maliyetleri "
        "dahildir, vergi hariçtir.",
        "",
        "| Seviye | Profil | WF CAGR | WF vol. | WF Sharpe | WF maks. düşüş | Statik CAGR |"
        " Statik vol. | Statik maks. düşüş |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for level, row in result["levels"].items():
        wf, st = row["walk_forward"], row["static_model"]
        lines.append(
            f"| {level} | {row['label']} | {pct(wf['cagr'])} | {pct(wf['volatility'])} | "
            f"{num(wf['sharpe'])} | {pct(wf['max_drawdown'])} | {pct(st['cagr'])} | "
            f"{pct(st['volatility'])} | {pct(st['max_drawdown'])} |"
        )
    lines += [
        "",
        "## Karşılaştırma ölçütleri (aynı dönem)",
        "",
        "| Ölçüt | CAGR | Vol. | Maks. düşüş |",
        "|---|---|---|---|",
    ]
    for name, s in result["benchmarks"].items():
        lines.append(
            f"| {name} | {pct(s['cagr'])} | {pct(s['volatility'])} | {pct(s['max_drawdown'])} |"
        )
    lines += [
        "",
        "## Okuma notları",
        "",
        f"* Bu test döneminde TÜFE+%3 hedefini walk-forward CAGR ile geçen seviye sayısı: "
        f"**{beat}/10**; XU100'ü geçen: **{beat_xu}/10** (daha düşük volatiliteyle).",
        "* Yüksek enflasyon döneminde nominal TL getiriler yüksektir; asıl karşılaştırma "
        "TÜFE+3 ve 60/40 ölçütleriyle yapılmalıdır.",
        "* Seviye arttıkça volatilite ve maksimum düşüşün artması beklenir; artmıyorsa model "
        "portföy bantları veya evren gözden geçirilmelidir.",
        "* Geçmiş performans gelecek için gösterge değildir; bilgi amaçlıdır.",
        "",
    ]
    return "\n".join(lines)


async def main_async() -> int:
    snap = SnapshotSource()
    market = MarketDataService(snap, snapshot=snap)
    result = await model_portfolio_performance(market)
    result["snapshot_end"] = snap.meta().get("end")
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8")
    OUT_DOC.write_text(render(result), encoding="utf-8")
    print(f"Yazıldı: {OUT_JSON.name} ({OUT_JSON.stat().st_size // 1024} KB), {OUT_DOC.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
