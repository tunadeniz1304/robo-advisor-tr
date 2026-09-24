"""Deterministic "Neden bu dağılım?" explanation cards.

Cards are built only from computed facts — risk profile, model portfolio,
optimiser risk contributions, Black-Litterman prior vs posterior, regime
tilt and cost/tax impact — so every number is traceable. The LLM may later
rewrite them fluently (task ``explain_allocation``) but never adds numbers.
"""

from __future__ import annotations

from typing import Any

from core.policy import get_policy
from llm.fmt import pct, tl


def _top(d: dict[str, float], n: int = 3) -> list[tuple[str, float]]:
    return sorted(((k, float(v)) for k, v in d.items()), key=lambda kv: kv[1], reverse=True)[:n]


def build_cards(
    *,
    level: dict[str, Any],
    optimization: dict[str, Any],
    risk_before: dict[str, Any] | None = None,
    risk_after: dict[str, Any] | None = None,
    costs: dict[str, Any] | None = None,
    regime: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build the ordered explanation cards of an allocation.

    Args:
        level: Effective level payload (``level``, ``label``, ``source``).
        optimization: :meth:`OptimizationResult.to_dict` payload.
        risk_before: ``{volatility, expected_return}`` of the current portfolio.
        risk_after: ``{volatility, expected_return}`` of the proposal.
        costs: ``{estimated_cost, estimated_tax, turnover}``.
        regime: ``{label, tilt}`` of the regime model.

    Returns:
        List of ``{kod, baslik, metin, veri}`` cards.
    """
    policy = get_policy()
    cards: list[dict[str, Any]] = []
    lv = int(level.get("level", 5))
    source = "uygunluk testinize" if level.get("source") == "profil" else "tahmini profilinize"
    cards.append(
        {
            "kod": "profil",
            "baslik": "Risk profiliniz",
            "metin": f"{source.capitalize()} göre risk seviyeniz {lv}/10 ({level.get('label')}).",
            "veri": {"seviye": lv, "kaynak": level.get("source")},
        }
    )
    model_level = int(optimization.get("model_level", lv))
    mw = optimization.get("model_class_weights") or {}
    cards.append(
        {
            "kod": "model",
            "baslik": "Model portföy",
            "metin": (
                f"Model {model_level} portföyü temel alındı: "
                + ", ".join(f"{policy.asset_classes.get(c, c)} {pct(w)}" for c, w in _top(mw, 4))
                + "."
            ),
            "veri": {"model_seviyesi": model_level, "sinif_agirliklari": mw},
        }
    )
    crc = optimization.get("class_risk_contributions") or {}
    if crc:
        top_c, top_v = _top(crc, 1)[0]
        cards.append(
            {
                "kod": "risk_katkisi",
                "baslik": "Risk dağılımı",
                "metin": (
                    f"{optimization.get('method_label')} ile portföy riskinin en büyük kısmı "
                    f"{policy.asset_classes.get(top_c, top_c)} sınıfından gelir ({pct(top_v)}). "
                    f"Beklenen yıllık oynaklık {pct(float(optimization.get('volatility', 0)))}."
                ),
                "veri": {"sinif_risk_katkisi": crc},
            }
        )
    bl = optimization.get("bl")
    if bl and bl.get("views"):
        parts = []
        for v in bl["views"][:3]:
            sym = v["symbol"]
            parts.append(
                f"{sym}: önsel {pct(bl['prior_returns'].get(sym, 0))} → sonsal "
                f"{pct(bl['posterior_returns'].get(sym, 0))}"
            )
        cards.append(
            {
                "kod": "bl_gorus",
                "baslik": "Ev görüşlerinin etkisi",
                "metin": "Black-Litterman görüşleri beklenen getirileri güncelledi: "
                + "; ".join(parts)
                + ".",
                "veri": {"gorusler": bl["views"]},
            }
        )
    if regime and regime.get("label"):
        tilt = float(regime.get("tilt", 0.0))
        direction = (
            "riskli varlıklara" if tilt > 0 else "savunmacı varlıklara" if tilt < 0 else "nötr"
        )
        cards.append(
            {
                "kod": "rejim",
                "baslik": "Piyasa rejimi",
                "metin": (
                    f"Rejim modeli ortamı '{regime['label']}' olarak sınıflandırıyor; dağılım "
                    f"{direction} {pct(abs(tilt))} eğildi."
                    if tilt
                    else f"Rejim modeli ortamı '{regime['label']}' olarak sınıflandırıyor; eğilim uygulanmadı."
                ),
                "veri": regime,
            }
        )
    if risk_before and risk_after and risk_before.get("volatility") is not None:
        vb, va = float(risk_before["volatility"]), float(risk_after.get("volatility", 0))
        verb = "düşürür" if va < vb else "artırır" if va > vb else "değiştirmez"
        cards.append(
            {
                "kod": "risk_degisimi",
                "baslik": "Beklenen etki",
                "metin": (
                    f"Bu değişiklik portföyünüzün beklenen oynaklığını {pct(vb)} seviyesinden "
                    f"{pct(va)} seviyesine {verb}."
                ),
                "veri": {"once": risk_before, "sonra": risk_after},
            }
        )
    if costs:
        cards.append(
            {
                "kod": "maliyet",
                "baslik": "Maliyet ve vergi",
                "metin": (
                    f"Tahmini işlem maliyeti {tl(float(costs.get('estimated_cost', 0)), 2)}, tahmini "
                    f"stopaj {tl(float(costs.get('estimated_tax', 0)), 2)}; devir oranı "
                    f"{pct(float(costs.get('turnover', 0)))}."
                ),
                "veri": costs,
            }
        )
    return cards


__all__ = ["build_cards"]
