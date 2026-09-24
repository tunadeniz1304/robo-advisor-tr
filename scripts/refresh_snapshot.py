"""Offline veri snapshot'ını yeniler (``data/snapshots``).

Kullanım::

    python scripts/refresh_snapshot.py            # ~10 yıl günlük veri
    python scripts/refresh_snapshot.py --period 5y

* Fiyatlar: Yahoo Finance'ten alttaki semboller indirilir, proxy enstrümanlar
  (TL para piyasası, TL tahvil, eurobond, gram altın) türetilir.
* Makro: ``EVDS_API_KEY`` tanımlıysa TCMB EVDS'ten, değilse depodaki
  yaklaşık ``macro_manual.csv`` dosyasından alınır.

Çıktılar: ``prices.csv.gz`` (tarih × enstrüman), ``macro.csv`` (aylık
TÜFE, politika faizi, USDTRY), ``meta.json``. Snapshot deterministiktir ve
testler ile internetsiz çalışma bu dosyaları kullanır.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except AttributeError:  # pragma: no cover
        pass

import pandas as pd  # noqa: E402

from core.config import Settings  # noqa: E402
from services.market_data.sources import (  # noqa: E402
    MACRO_FILE,
    MACRO_MANUAL_FILE,
    META_FILE,
    PRICES_FILE,
    SNAPSHOT_DIR,
    EvdsSource,
    LiveYahooSource,
    SnapshotSource,
    read_macro_csv,
)
from services.market_data.universe import UNDERLYING_YAHOO  # noqa: E402


def build_macro(settings: Settings, directory: Path) -> tuple[pd.DataFrame, str]:
    """Monthly macro frame from EVDS (if keyed) or the manual CSV."""
    if settings.evds_api_key:
        try:
            frame = EvdsSource(settings.evds_api_key).fetch(date(2015, 12, 1), date.today())
            return frame, "evds"
        except Exception as exc:  # noqa: BLE001
            print(f"EVDS alınamadı ({type(exc).__name__}); yaklaşık seri kullanılacak.")
    return read_macro_csv(directory / MACRO_MANUAL_FILE), "manual_approx"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--period", default="10y")
    parser.add_argument("--out", default=str(SNAPSHOT_DIR))
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    settings = Settings.load()
    macro, macro_source = build_macro(settings, out)
    macro_provider = SnapshotSource(out)
    macro_provider._macro = macro  # türetme aynı makro seriyi kullansın

    live = LiveYahooSource(macro_provider=macro_provider, period=args.period)
    panel = live.fetch_panel(list(UNDERLYING_YAHOO))
    panel = panel.dropna(how="all").ffill()
    panel = panel[panel.index >= panel.dropna(subset=["XU100.IS"]).index.min()]
    panel.index.name = "date"

    usdtry_m = panel["USDTRY"].resample("ME").last()
    macro_out = macro.copy()
    macro_out["USDTRY"] = usdtry_m.reindex(macro_out.index)
    macro_out.index.name = "date"

    panel.round(6).to_csv(out / PRICES_FILE, compression="gzip")
    with (out / MACRO_FILE).open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"# Kaynak: {macro_source}. TÜFE 2003=100, POLICY_RATE kesir, USDTRY ay sonu.\n")
        macro_out.round(6).to_csv(fh)
    meta = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "period": args.period,
        "start": panel.index.min().date().isoformat(),
        "end": panel.index.max().date().isoformat(),
        "rows": int(panel.shape[0]),
        "instruments": list(panel.columns),
        "price_source": "yahoo_finance+proxy",
        "macro_source": macro_source,
        "note": "Bilgi amaçlıdır. Proxy seriler gerçek fon getirilerini birebir yansıtmaz.",
    }
    (out / META_FILE).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Snapshot yazıldı: {meta['rows']} gün × {len(panel.columns)} enstrüman "
        f"({meta['start']} → {meta['end']}), makro={macro_source}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
