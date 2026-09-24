"""Deterministic demo LLM — the production fallback when no key is present.

:class:`DeterministicLLM` implements the same :class:`llm.clients.LLMClient`
interface as the live clients. It reads the ``[GOREV:…]`` marker and the
``<data>`` JSON of the prompt and renders a meaningful Turkish answer from
**the real numbers in that data** with fixed templates:

    * no network, no randomness — identical input, identical output;
    * every number it writes comes from the context, so its output always
      passes the number guard (:mod:`llm.guard`);
    * for the copilot it even speaks the tool-calling protocol: it picks
      tools by intent keywords, then composes the answer from tool results.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from llm.clients import LLMClient, LLMResponse, ToolCall
from llm.fmt import pct, tl
from llm.prompts import parse_data, parse_task


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _top_items(weights: dict[str, Any], n: int = 3) -> list[tuple[str, float]]:
    items = [(str(k), _f(v)) for k, v in (weights or {}).items()]
    items.sort(key=lambda kv: kv[1], reverse=True)
    return items[:n]


# ----------------------------------------------------------------- renderers


def render_rebalance_rationale(data: dict[str, Any]) -> dict[str, Any]:
    level = data.get("risk_seviyesi")
    category = data.get("risk_kategorisi") or "dengeli"
    method = data.get("yontem") or "optimizasyon"
    weights = data.get("hedef_agirliklar") or {}
    orders = data.get("emirler") or []
    top = _top_items(weights, 3)
    level_text = f"{level}/10 " if level is not None else ""
    if top:
        head = ", ".join(f"{sym} ({pct(w)})" for sym, w in top)
        ozet = (
            f"Risk seviyeniz {level_text}({category}) için {method} yöntemiyle hesaplanan "
            f"hedef dağılımda öne çıkan varlıklar: {head}. Önerilen {len(orders)} emir, "
            "portföyü bu hedefe en az işlemle yaklaştırır."
        )
    else:
        ozet = (
            f"Risk seviyeniz {level_text}({category}) için portföy hedef dağılıma yakın; "
            "şu an ek işlem gerekmiyor."
        )
    gerekceler: list[str] = []
    vol_after = data.get("beklenen_volatilite")
    vol_before = data.get("onceki_volatilite")
    if vol_after is not None and vol_before is not None:
        gerekceler.append(
            f"Beklenen yıllık volatilite {pct(_f(vol_before))} seviyesinden "
            f"{pct(_f(vol_after))} seviyesine gelir."
        )
    elif vol_after is not None:
        gerekceler.append(f"Hedef dağılımın beklenen yıllık volatilitesi {pct(_f(vol_after))}.")
    exp_ret = data.get("beklenen_getiri")
    if exp_ret is not None:
        gerekceler.append(f"Model varsayımlarıyla beklenen yıllık getiri {pct(_f(exp_ret))}.")
    rc = data.get("en_buyuk_risk_katkisi")
    if isinstance(rc, dict) and rc.get("sembol"):
        gerekceler.append(
            f"En büyük risk katkısı {rc['sembol']} varlığından gelir ({pct(_f(rc.get('pay')))})."
        )
    cost = data.get("toplam_maliyet")
    tax = data.get("toplam_vergi")
    if cost is not None:
        extra = f", tahmini vergi {tl(_f(tax))}" if tax is not None else ""
        gerekceler.append(f"Tahmini işlem maliyeti {tl(_f(cost))}{extra}.")
    if not gerekceler:
        gerekceler.append("Dağılım, uygunluk seviyenizin izin verdiği sınırlar içindedir.")
    riskler = [
        "Piyasa koşulları değişirse beklenen getiri ve oynaklık varsayımları sapabilir.",
        "Öneri, onaylanana kadar yürütülmez; fiyatlar değişirse öneri yeniden hesaplanır.",
    ]
    return {"ozet": ozet, "gerekceler": gerekceler[:6], "riskler": riskler}


def render_market_commentary(data: dict[str, Any]) -> dict[str, Any]:
    market = data.get("piyasa") or {}
    macro = data.get("makro") or {}
    rows = [
        (str(sym), _f(v.get("getiri_1_ay")), _f(v.get("volatilite")))
        for sym, v in market.items()
        if isinstance(v, dict)
    ]
    rows.sort(key=lambda r: r[1], reverse=True)
    maddeler: list[str] = []
    if rows:
        best, worst = rows[0], rows[-1]
        ozet = (
            f"Son bir ayda en güçlü performans {best[0]} ({pct(best[1])}), en zayıf "
            f"performans {worst[0]} ({pct(worst[1])}) tarafında gerçekleşti."
        )
        for sym, ret, vol in rows[:5]:
            maddeler.append(f"{sym}: 1 aylık getiri {pct(ret)}, yıllık oynaklık {pct(vol)}.")
    else:
        ozet = "Piyasa verisi sınırlı; güncel fiyatlar geldiğinde yorum güncellenecek."
    if macro.get("politika_faizi") is not None:
        maddeler.append(f"TCMB politika faizi {pct(_f(macro['politika_faizi']))}.")
    if macro.get("tufe_yillik") is not None:
        maddeler.append(f"Yıllık TÜFE {pct(_f(macro['tufe_yillik']))}.")
    regime = data.get("rejim")
    if isinstance(regime, dict) and regime.get("etiket"):
        maddeler.append(f"Rejim modeli mevcut ortamı '{regime['etiket']}' olarak sınıflandırıyor.")
    return {"baslik": "Piyasa Görünümü", "ozet": ozet, "maddeler": maddeler[:8]}


def render_goal_report(data: dict[str, Any]) -> dict[str, Any]:
    goal = data.get("hedef") or {}
    name = goal.get("ad") or goal.get("tur") or "hedefiniz"
    prob = data.get("basari_olasiligi")
    p50 = data.get("p50")
    target = goal.get("hedef_tutar")
    ozet_parts = [f"{name} için yapılan simülasyonda"]
    if prob is not None:
        ozet_parts.append(f"hedefe ulaşma olasılığı {pct(_f(prob))}.")
    else:
        ozet_parts.append("başarı olasılığı hesaplandı.")
    if p50 is not None and target is not None:
        ozet_parts.append(
            f"Medyan senaryoda reel birikim {tl(_f(p50))}, hedef ise {tl(_f(target))}."
        )
    oneriler: list[str] = []
    needed = data.get("gerekli_aylik_katki")
    current = data.get("aylik_katki")
    if needed is not None and current is not None and _f(needed) > _f(current):
        oneriler.append(
            f"Hedefe güvenle ulaşmak için aylık katkıyı {tl(_f(current))} yerine "
            f"{tl(_f(needed))} seviyesine çıkarmayı değerlendirin."
        )
    elif needed is not None:
        oneriler.append(f"Mevcut katkı planı yeterli görünüyor ({tl(_f(needed))} gerekli).")
    for w in data.get("what_if") or []:
        if isinstance(w, dict) and w.get("aciklama") and w.get("basari_olasiligi") is not None:
            oneriler.append(f"{w['aciklama']}: başarı olasılığı {pct(_f(w['basari_olasiligi']))}.")
    if not oneriler:
        oneriler.append("Katkıları düzenli sürdürmek başarı olasılığını korur.")
    return {"ozet": " ".join(ozet_parts), "oneriler": oneriler[:5]}


def render_explain_allocation(data: dict[str, Any]) -> dict[str, Any]:
    cards = data.get("kartlar") or []
    maddeler = [
        f"{c.get('baslik')}: {c.get('metin')}"
        for c in cards
        if isinstance(c, dict) and c.get("baslik") and c.get("metin")
    ]
    ozet = (
        "Bu dağılım; risk profiliniz, model portföy seviyeniz ve optimizasyonun risk "
        "bütçesi birlikte değerlendirilerek oluşturuldu."
    )
    if not maddeler:
        maddeler = ["Açıklama kartı bulunamadı."]
    return {"ozet": ozet, "maddeler": maddeler[:8]}


def render_portfolio_letter(data: dict[str, Any]) -> dict[str, Any]:
    port = data.get("portfoy") or {}
    perf = data.get("performans") or {}
    risk = data.get("risk") or {}
    total = port.get("toplam_deger")
    giris = (
        f"Portföyünüzün güncel değeri {tl(_f(total))}."
        if total is not None
        else "Portföyünüzün haftalık özeti aşağıdadır."
    )
    performans = (
        f"Dönem getirisi {pct(_f(perf.get('toplam_getiri')))}, yıllık oynaklık "
        f"{pct(_f(perf.get('volatilite')))}."
        if perf
        else "Performans verisi henüz yeterli değil."
    )
    risk_text = (
        f"Maksimum düşüş {pct(_f(risk.get('maks_dusus')))}, %95 günlük VaR "
        f"{pct(_f(risk.get('var_95')))}."
        if risk
        else "Risk göstergeleri hedef aralıkta."
    )
    commentary = render_market_commentary(data)
    return {
        "baslik": "Haftalık Portföy Mektubu",
        "giris": giris,
        "performans": performans,
        "risk": risk_text,
        "gorunum": commentary["ozet"],
        "sonuc": "Portföyünüz risk profilinizle uyumlu şekilde izlenmeye devam ediyor.",
    }


def render_bl_views(data: dict[str, Any]) -> dict[str, Any]:
    market = data.get("piyasa") or {}
    rows = [
        (str(sym), _f(v.get("getiri_12_ay")), _f(v.get("volatilite")))
        for sym, v in market.items()
        if isinstance(v, dict) and v.get("getiri_12_ay") is not None
    ]
    rows.sort(key=lambda r: r[1], reverse=True)
    views = []
    for sym, ret, _vol in rows[:2]:
        views.append(
            {
                "varlik": sym,
                "beklenen_getiri": max(-0.9, min(3.0, ret)),
                "guven": 0.4,
                "gerekce": f"{sym} son 12 ayda {pct(ret)} getiri sağladı; momentum görüşü.",
            }
        )
    return {"gorusler": views}


RENDERERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "rebalance_rationale": render_rebalance_rationale,
    "market_commentary": render_market_commentary,
    "goal_report": render_goal_report,
    "explain_allocation": render_explain_allocation,
    "portfolio_letter": render_portfolio_letter,
    "bl_view_suggestions": render_bl_views,
}

# ----------------------------------------------------------------- copilot

# (anahtar kelimeler, araç adı, argüman üretici)
_INTENTS: tuple[tuple[tuple[str, ...], str, Callable[[str], dict[str, Any]]], ...] = (
    (("emeklil", "hedef", "yetiş", "birikim", "ev al"), "simulate_goal", lambda q: {}),
    (
        ("dolar", "kur", "usd", "euro"),
        "run_stress",
        lambda q: {"scenario": "usdtry_up_30"},
    ),
    (("faiz",), "run_stress", lambda q: {"scenario": "rate_up_500bp"}),
    (
        ("borsa", "bist", "çöker", "düşer", "stres", "kriz"),
        "run_stress",
        lambda q: {"scenario": "bist_down_25"},
    ),
    (("neden", "niye", "niçin", "açıkla"), "explain_allocation", lambda q: {}),
    (("dengele", "rebalance", "öneri", "işlem"), "preview_rebalance", lambda q: {}),
    (("piyasa", "gündem", "yorum", "makro"), "get_market_summary", lambda q: {}),
    (("risk profil", "risk seviye", "uygunluk"), "get_risk_profile", lambda q: {}),
    (("optimiz", "dağılım", "ağırlık"), "run_optimization", lambda q: {}),
)


def pick_tools(question: str, available: set[str]) -> list[tuple[str, dict[str, Any]]]:
    """Select tools for a question by keyword intent (deterministic)."""
    q = question.lower()
    chosen: list[tuple[str, dict[str, Any]]] = []
    for keywords, tool, args in _INTENTS:
        if tool in available and any(k in q for k in keywords):
            if all(tool != t for t, _ in chosen):
                chosen.append((tool, args(q)))
    if not chosen and "get_portfolio" in available:
        chosen.append(("get_portfolio", {}))
    return chosen[:3]


def _summarise_tool(name: str, result: dict[str, Any]) -> str:
    if "hata" in result:
        return f"{name} çalıştırılamadı: {result['hata']}"
    if name == "simulate_goal":
        goal = result.get("hedef") or {}
        text = (
            f"'{goal.get('ad') or goal.get('tur') or 'Hedef'}' için başarı olasılığı "
            f"{pct(_f(result.get('basari_olasiligi')))}."
        )
        if result.get("gerekli_aylik_katki") is not None:
            text += f" Gerekli aylık katkı {tl(_f(result['gerekli_aylik_katki']))}."
        return text
    if name == "run_stress":
        return (
            f"'{result.get('senaryo_adi') or result.get('senaryo')}' senaryosunda portföy "
            f"etkisi {pct(_f(result.get('pnl_orani')))} ({tl(_f(result.get('pnl')))})."
        )
    if name == "explain_allocation":
        cards = result.get("kartlar") or []
        return " ".join(f"{c.get('baslik')}: {c.get('metin')}" for c in cards[:4]) or (
            "Açıklama kartı yok."
        )
    if name == "preview_rebalance":
        return (
            f"Taslak öneri #{result.get('oneri_id')} oluşturuldu: {len(result.get('emirler') or [])} "
            f"emir, tahmini maliyet {tl(_f(result.get('toplam_maliyet')))}. Yürütme için "
            "onayınız gerekir."
        )
    if name == "get_market_summary":
        return render_market_commentary(result)["ozet"]
    if name == "get_risk_profile":
        return (
            f"Risk seviyeniz {result.get('seviye')}/10 ({result.get('kategori') or '-'}); "
            f"profil geçerlilik tarihi {result.get('gecerlilik') or '-'}."
        )
    if name == "run_optimization":
        top = _top_items(result.get("agirliklar") or {}, 3)
        return "Optimizasyon sonucu öne çıkan ağırlıklar: " + ", ".join(
            f"{s} {pct(w)}" for s, w in top
        )
    if name == "get_portfolio":
        return (
            f"Portföy değeri {tl(_f(result.get('toplam_deger')))}, nakit "
            f"{tl(_f(result.get('nakit')))}."
        )
    return f"{name} sonucu alındı."


class DeterministicLLM(LLMClient):
    """Template-based, network-free LLM used in demo mode and as fallback.

    Args:
        text: Optional fixed text returned for prompts without a task marker
            (kept for backward compatibility with older tests).
    """

    mode = "demo"
    model = "deterministic-demo"

    def __init__(self, text: str | None = None) -> None:
        self._text = text or (
            "Hedef dağılım, risk profilinizle uyumludur ve uygunluk sınırları içinde "
            "kalır. Bu içerik bilgi amaçlıdır, yatırım tavsiyesi değildir."
        )
        self.calls: list[tuple[str, str]] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        json_mode: bool = False,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        system = next((str(m.get("content")) for m in messages if m.get("role") == "system"), "")
        user = next(
            (str(m.get("content")) for m in reversed(messages) if m.get("role") == "user"), ""
        )
        self.calls.append((system, user))
        task = parse_task(system)

        if task == "copilot":
            return self._copilot_turn(messages, tools or [])

        renderer = RENDERERS.get(task or "")
        if renderer is None:
            return LLMResponse(content=self._text, model=self.model)
        payload = renderer(parse_data(user))
        return LLMResponse(content=json.dumps(payload, ensure_ascii=False), model=self.model)

    def _copilot_turn(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResponse:
        # Son kullanıcı mesajından sonra araç sonucu var mı?
        last_user = max(i for i, m in enumerate(messages) if m.get("role") == "user")
        tool_msgs = [m for m in messages[last_user + 1 :] if m.get("role") == "tool"]
        question = str(messages[last_user].get("content") or "")
        # ReAct metin protokolünde soru <soru> etiketi içinde gelir.
        available = {t.get("function", {}).get("name") for t in tools}
        available.discard(None)
        if not tool_msgs and available:
            picks = pick_tools(question, {str(a) for a in available})
            calls = [
                ToolCall(id=f"demo_{i}_{name}", name=name, arguments=args)
                for i, (name, args) in enumerate(picks)
            ]
            return LLMResponse(content="", tool_calls=calls, model=self.model)

        parts: list[str] = []
        for msg in tool_msgs:
            name = str(msg.get("name") or "")
            try:
                result = json.loads(str(msg.get("content") or "{}"))
            except json.JSONDecodeError:
                result = {}
            parts.append(_summarise_tool(name, result if isinstance(result, dict) else {}))
        answer = " ".join(parts) or "Sorunuzu yanıtlamak için yeterli veri bulunamadı."
        return LLMResponse(
            content=json.dumps({"yanit": answer}, ensure_ascii=False), model=self.model
        )


__all__ = ["DeterministicLLM", "RENDERERS", "pick_tools"]
