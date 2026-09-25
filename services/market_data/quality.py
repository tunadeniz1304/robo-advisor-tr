"""Data-quality checks for price series.

Every series of the panel is checked for:

* ``gap``   — more than ``gap_bdays`` missing business days between two
  observations (holidays are shorter than that), or a run of that many
  unchanged prices (a forward-filled hole or a trading halt);
* ``jump``  — a daily log return that is both large in absolute terms
  (``jump_min_abs``) and extreme against the series' own robust volatility
  (median absolute deviation z-score above ``jump_z``);
* ``split_suspect`` — a jump whose price ratio is close to 1/k or k
  (k = 2…10): the signature of an unadjusted split or bonus issue;
* ``stale`` — the last observation is older than ``stale_days`` calendar
  days at ``as_of``.

Extreme moves older than ``recent_days`` are reported with severity
``info`` (e.g. the 2018 and 2021 FX shocks are real events, not data errors)
and do not turn the series status into a warning.

Thresholds live in ``[data_quality]`` of the policy file. The result is
served at ``GET /api/v1/data/quality`` and summarised in the UI badge.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from core.policy import InvestmentPolicy, get_policy

MAD_TO_SIGMA = 1.4826
SPLIT_RATIOS = tuple(range(2, 11))
MAX_ISSUES_PER_KIND = 5


def _cfg(policy: InvestmentPolicy | None) -> dict[str, float]:
    raw = (policy or get_policy()).raw.get("data_quality", {})
    return {k: float(v) for k, v in raw.items()}


def _split_like(ratio: float, tolerance: float) -> bool:
    for k in SPLIT_RATIOS:
        if abs(ratio - k) / k < tolerance or abs(ratio - 1.0 / k) * k < tolerance:
            return True
    return False


def assess_series(
    name: str,
    series: pd.Series,
    *,
    as_of: pd.Timestamp,
    policy: InvestmentPolicy | None = None,
) -> dict[str, Any]:
    """Quality report of one price series."""
    cfg = _cfg(policy)
    s = series.dropna().sort_index()
    s = s[s > 0]
    issues: list[dict[str, Any]] = []
    if s.shape[0] < 2:
        return {
            "symbol": name,
            "status": "error",
            "rows": int(s.shape[0]),
            "first_date": None,
            "last_date": None,
            "issues": [{"kind": "empty", "detail": "Seride yeterli gözlem yok."}],
        }
    idx = pd.DatetimeIndex(s.index)
    # boşluklar: ardışık gözlemler arasındaki kayıp iş günü sayısı
    days = idx.values.astype("datetime64[D]")
    missing = np.busday_count(days[:-1], days[1:]) - 1
    for pos in np.flatnonzero(missing > cfg["gap_bdays"])[:MAX_ISSUES_PER_KIND]:
        issues.append(
            {
                "kind": "gap",
                "date": idx[pos + 1].date().isoformat(),
                "detail": f"{int(missing[pos])} iş günü veri yok ({idx[pos].date()} sonrası).",
            }
        )
    # sıçramalar ve bölünme şüphesi
    logret = np.log(s).diff().dropna()
    # İleri doldurulmuş boşluklar: uzun süre hiç değişmeyen fiyat (eksik veri / işlem durdurma)
    flat = (logret == 0).astype(int)
    runs = flat.groupby((flat != flat.shift()).cumsum()).cumsum()
    if not runs.empty and int(runs.max()) > cfg["gap_bdays"]:
        end = runs.idxmax()
        issues.append(
            {
                "kind": "gap",
                "date": pd.Timestamp(end).date().isoformat(),
                "detail": f"{int(runs.max())} gün boyunca değişmeyen fiyat (eksik veri olabilir).",
            }
        )
    med = float(logret.median())
    # Sabit getirili (ör. para piyasası) serilerde MAD≈0 olur; taban oynaklık kullanılır.
    mad = max(float((logret - med).abs().median()) * MAD_TO_SIGMA, cfg["min_sigma"])
    if not logret.empty:
        z = (logret - med) / mad
        flagged = logret[(z.abs() > cfg["jump_z"]) & (logret.abs() > cfg["jump_min_abs"])]
        for when, value in flagged.iloc[:MAX_ISSUES_PER_KIND].items():
            ratio = float(np.exp(value))
            kind = "split_suspect" if _split_like(ratio, cfg["split_tolerance"]) else "jump"
            recent = (pd.Timestamp(as_of) - pd.Timestamp(when)).days <= cfg["recent_days"]
            issues.append(
                {
                    "kind": kind,
                    "severity": "warning" if recent or kind == "split_suspect" else "info",
                    "date": pd.Timestamp(when).date().isoformat(),
                    "detail": f"Günlük değişim {ratio - 1:+.1%} (z={float(z[when]):.1f}).",
                }
            )
    age = int((pd.Timestamp(as_of).normalize() - idx[-1].normalize()).days)
    if age > cfg["stale_days"]:
        issues.append(
            {
                "kind": "stale",
                "date": idx[-1].date().isoformat(),
                "detail": f"Son gözlem {age} gün önce.",
            }
        )
    for issue in issues:
        issue.setdefault("severity", "warning")
    status = "ok"
    if any(i["severity"] == "warning" for i in issues):
        status = "error" if age > cfg["stale_error_days"] else "warning"
    return {
        "symbol": name,
        "status": status,
        "rows": int(s.shape[0]),
        "first_date": idx[0].date().isoformat(),
        "last_date": idx[-1].date().isoformat(),
        "age_days": age,
        "issues": issues,
    }


def assess_panel(
    panel: pd.DataFrame,
    *,
    as_of: pd.Timestamp,
    meta: dict[str, Any] | None = None,
    policy: InvestmentPolicy | None = None,
) -> dict[str, Any]:
    """Quality report of a price panel, annotated with snapshot provenance."""
    from services.market_data.universe import BY_SYMBOL

    series_meta = (meta or {}).get("series", {})
    reports = []
    for col in panel.columns:
        rep = assess_series(str(col), panel[col], as_of=as_of, policy=policy)
        info = series_meta.get(col, {})
        spec = BY_SYMBOL.get(str(col))
        rep["source"] = info.get("source") or (spec.source if spec else "unknown")
        rep["is_proxy"] = bool(info.get("is_proxy", False))
        rep["proxy_until"] = info.get("proxy_until")
        reports.append(rep)
    order = {"ok": 0, "warning": 1, "error": 2}
    worst = max((r["status"] for r in reports), key=order.__getitem__, default="ok")
    return {
        "status": worst,
        "checked_at": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds"),
        "as_of": pd.Timestamp(as_of).date().isoformat(),
        "counts": {k: sum(r["status"] == k for r in reports) for k in order},
        "series": reports,
    }


__all__ = ["assess_panel", "assess_series"]
