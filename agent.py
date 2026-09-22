# -*- coding: utf-8 -*-
"""
Otonom Finansal Danışman (Robo-Advisor) - Ajan Tanımları
========================================================
Framework: LangChain (@tool + FakeListChatModel sahte LLM).

Üç ajan:
  1. Piyasa Analisti  (MarketAnalystAgent)   -> piyasa görünümü, risk iştahı
  2. Risk Değerlendiricisi (RiskAssessorAgent)-> anket skoru -> risk profili + hedef dağılım
  3. Portföy Yöneticisi (PortfolioManagerAgent)-> hedef vs mevcut sapma -> yeniden dengeleme emirleri

Tasarım ilkesi:
  - Tüm RAKAMLAR deterministik saf Python matematiği ile hesaplanır
    (momentum, volatilite, anket skoru, sapma, alım/satım emirleri).
  - Doğal dil gerekçeleri LangChain'in sahte LLM (FakeListChatModel) katmanından,
    aynı girişte birebir aynı şekilde döner -> tamamen tekrarlanabilir, çevrimdışı.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from statistics import mean, stdev
from typing import Any, Callable, Dict, List

from langchain_core.tools import tool
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_community.chat_models import FakeListChatModel


# =============================================================================
# 1) SAHTE PİYASA VERİSİ (mock data)
# =============================================================================

# Her fon için 20 günlük kapanış fiyat serisi (TL).
FUNDS: Dict[str, Dict[str, Any]] = {
    "TTA": {  # Teknoloji Hisse Senedi Fonu
        "ad": "TTA Teknoloji Hisse Senedi Fonu",
        "sektor": "hisse-teknoloji",
        "urun_sinifi": "hisse",
        "expense_ratio": 0.015,
        "fiyat": [18.4, 18.8, 18.5, 19.2, 19.8, 19.5, 20.1, 20.7, 20.3, 21.0,
                  21.4, 21.8, 21.5, 22.2, 22.9, 22.6, 23.4, 23.9, 24.1, 24.8],
    },
    "TDB": {  # Değişken Fon (hisse + sabit getiri karışımı)
        "ad": "TDB Değişken Fon",
        "sektor": "değişken",
        "urun_sinifi": "değişken",
        "expense_ratio": 0.012,
        "fiyat": [12.1, 12.0, 12.3, 12.2, 12.5, 12.4, 12.6, 12.5, 12.8, 12.7,
                  12.9, 13.0, 12.8, 13.1, 13.0, 13.3, 13.2, 13.4, 13.3, 13.6],
    },
    "TKA": {  # Katılım (faizsiz) Fon
        "ad": "TKA Katılım Fonu",
        "sektor": "katılım",
        "urun_sinifi": "katilim",
        "expense_ratio": 0.010,
        "fiyat": [9.8, 9.7, 9.9, 9.8, 10.0, 9.9, 10.1, 10.0, 10.2, 10.1,
                  10.3, 10.2, 10.4, 10.3, 10.5, 10.4, 10.6, 10.5, 10.7, 10.6],
    },
    "TBA": {  # Borçlanma Araçları (Tahvil-Bono) Fonu
        "ad": "TBA Borçlanma Araçları Fonu",
        "sektor": "sabit-getiri",
        "urun_sinifi": "sabit-getiri",
        "expense_ratio": 0.008,
        "fiyat": [7.5, 7.51, 7.52, 7.5, 7.53, 7.54, 7.53, 7.55, 7.56, 7.55,
                  7.57, 7.58, 7.57, 7.59, 7.6, 7.59, 7.61, 7.62, 7.61, 7.63],
    },
    "TAL": {  # Altın Fonu
        "ad": "TAL Altın Fonu",
        "sektor": "emtia",
        "urun_sinifi": "emtia-altin",
        "expense_ratio": 0.011,
        "fiyat": [15.0, 15.2, 15.1, 15.5, 15.4, 15.8, 15.7, 16.0, 16.2, 16.1,
                  16.4, 16.3, 16.7, 16.6, 16.9, 17.0, 16.8, 17.2, 17.1, 17.4],
    },
    "TUV": {  # Uluslararası Varlık Fonu
        "ad": "TUV Uluslararası Varlık Fonu",
        "sektor": "yurt-dışı",
        "urun_sinifi": "hisse",
        "expense_ratio": 0.016,
        "fiyat": [22.0, 21.8, 22.3, 22.1, 22.6, 22.4, 22.9, 22.7, 23.2, 23.0,
                  23.5, 23.3, 23.8, 23.6, 24.0, 23.8, 24.3, 24.1, 24.6, 24.4],
    },
}

# Haber akışı: (başlık, duyarlılık -1..+1, ilgili sektörler)
NEWS_FEED: List[tuple] = [
    ("Enflasyon beklentilerin altında kaldı; Merkez Bankası faiz koridoru sabit tuttu", 0.75, ["sabit-getiri", "hisse"]),
    ("Teknoloji ihracatı rekor kırdı; yazılım firmaları pozitif ayrışıyor", 0.70, ["hisse-teknoloji"]),
    ("Küresel piyasalarda risk iştahı güçlü; gelişen piyasalara fon girişi arttı", 0.55, ["hisse", "yurt-dışı"]),
    ("Altın fiyatları jeopolitik belirsizlikle destek buluyor", 0.40, ["emtia-altin"]),
    ("Borsa İstanbul'da dalgalanma bir miktar arttı, endeks yatay seyretti", -0.20, ["hisse", "değişken"]),
    ("Tahvil getirileri düşüş eğiliminde; sabit getiri fonları ilgi görüyor", 0.45, ["sabit-getiri"]),
]

# Müşteri risk anketi: 5 soru, her soru 0-3 puan
SURVEY_QUESTIONS: List[Dict[str, Any]] = [
    {"soru": "Yatırım deneyiminiz nedir?", "secenekler": ["Yok", "Biraz", "Orta", "İleri"]},
    {"soru": "Yatırım ufkunuz ne kadar?", "secenekler": ["< 1 yıl", "1-3 yıl", "3-7 yıl", "7+ yıl"]},
    {"soru": "Kısa vadeli %10 düşüşte ne yaparsınız?", "secenekler": ["Tümünü satarım", "Yarısını satarım", "Tutarım", "İlave alırım"]},
    {"soru": "Yıllık gelirinizin ne kadarını yatırabilirsiniz?", "secenekler": ["%5'ten az", "%5-10", "%10-20", "%20'den fazla"]},
    {"soru": "Aşağıdaki getiri/risk ikilisini nasıl değerlendirirsiniz?", "secenekler": ["Düşük risk, düşük getiri", "Orta risk, orta getiri", "Yüksek risk, yüksek getiri", "Çok yüksek risk, çok yüksek getiri"]},
]

# Anket puanı (0-20) -> risk profili + hedef varlık dağılımı
# class anahtarları FUNDS urun_sinifi ile aynıdır.
RISK_PROFILES: Dict[str, Dict[str, Any]] = {
    "Muhafazakar": {
        "max_equity": 0.20,
        "hedef": {"sabit-getiri": 0.40, "değişken": 0.30, "hisse": 0.10,
                  "katilim": 0.10, "emtia-altin": 0.10},
    },
    "Dengeli": {
        "max_equity": 0.40,
        "hedef": {"sabit-getiri": 0.25, "değişken": 0.25, "hisse": 0.25,
                  "katilim": 0.15, "emtia-altin": 0.10},
    },
    "Büyüme": {
        "max_equity": 0.60,
        "hedef": {"sabit-getiri": 0.15, "değişken": 0.20, "hisse": 0.40,
                  "katilim": 0.10, "emtia-altin": 0.15},
    },
    "Agresif": {
        "max_equity": 0.80,
        "hedef": {"sabit-getiri": 0.05, "değişken": 0.15, "hisse": 0.55,
                  "katilim": 0.05, "emtia-altin": 0.20},
    },
}


def risk_profile_for_score(score: int) -> str:
    """0-20 -> risk profili adı."""
    if score <= 5:
        return "Muhafazakar"
    if score <= 10:
        return "Dengeli"
    if score <= 15:
        return "Büyüme"
    return "Agresif"


# =============================================================================
# 2) DETERMİNİSTİK HESAP MOTORLARI (saf Python; bütün rakamlar buradan)
# =============================================================================

def fund_daily_returns(prices: List[float]) -> List[float]:
    return [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, len(prices))]


def fund_metrics(fund: Dict[str, Any]) -> Dict[str, float]:
    """Tek fon için: son fiyat, günlük getiri, 20-(işlem) günlük momentum,
    yıllıklandırılmış volatilite."""
    prices = fund["fiyat"]
    last = prices[-1]
    prev = prices[-2]
    daily_ret = (last - prev) / prev
    momentum = (last - prices[0]) / prices[0]
    rets = fund_daily_returns(prices)
    vol = (stdev(rets) * math.sqrt(252)) if len(rets) > 1 else 0.0
    return {"son_fiyat": last, "gunluk_getiri": daily_ret,
            "momentum": momentum, "volatilite": vol}


def sector_sentiment() -> Dict[str, float]:
    """NEWS_FEED'den sektör başına ortalama duyarlılık."""
    acc: Dict[str, List[float]] = {}
    for headline, sent, sectors in NEWS_FEED:
        for s in sectors:
            acc.setdefault(s, []).append(sent)
    return {s: mean(v) for s, v in acc.items()}


def classify_sentiment(score: float) -> str:
    if score > 0.25:
        return "pozitif"
    if score < -0.25:
        return "negatif"
    return "nötr"


def compute_market_snapshot() -> Dict[str, Any]:
    """Her fon için görünüm + piyasa rejimi (risk-on / risk-off)."""
    sent = sector_sentiment()
    outlook = {}
    sentiments = []
    for key, fund in FUNDS.items():
        m = fund_metrics(fund)
        sektor = fund["sektor"]
        s_score = sent.get(sektor, 0.0)
        s_label = classify_sentiment(s_score)
        outlook[key] = {
            "ad": fund["ad"],
            "urun_sinifi": fund["urun_sinifi"],
            "son_fiyat": m["son_fiyat"],
            "gunluk_getiri": m["gunluk_getiri"],
            "momentum": m["momentum"],
            "volatilite": m["volatilite"],
            "duyarlilik": s_score,
            "gorunum": s_label,
        }
        sentiments.append(s_score)
    # Piyasa rejimi: ortalama volatilite düşük VE sektör duyarlılıkları ağırlıklı pozitif
    vols = [outlook[k]["volatilite"] for k in outlook]
    avg_vol = mean(vols)
    avg_sent = mean(sentiments)
    risk_on = avg_sent > 0.25 and avg_vol < 0.30
    snapshot = {
        "outlook": outlook,
        "ort_volatilite": avg_vol,
        "ort_duyarlilik": avg_sent,
        "rejim": "risk-on" if risk_on else "risk-off",
        "sektor_duyarliliklari": sent,
    }
    return snapshot


def score_survey(answers: List[int]) -> int:
    """5 cevabın (her biri 0-3) toplamı -> 0-20."""
    return sum(max(0, min(3, int(a))) for a in answers)


# =============================================================================
# 3) LANGCHAIN @tool VERİ ERİŞİM FONKSİYONLARI (ja)anlara enjekte edilir)
# =============================================================================

@tool
def get_market_snapshot() -> str:
    """Piyasa görünümü: her fon için momentum/volatilite/duyarlılık ve rejim."""
    return json.dumps(compute_market_snapshot(), ensure_ascii=False)


@tool
def get_news_feed() -> str:
    """Haber akışı: başlık + duyarlılık."""
    return "\n".join(f"{h} (duyarlılık {s:+.2f})" for h, s, _ in NEWS_FEED)


@tool
def get_client_survey() -> str:
    """Sahte müşteri anketi cevapları (her soru için seçenek indeksi 0-3)."""
    answers = [3, 3, 2, 3, 2]  # toplam 13 -> Büyüme profili
    q = [f"{i + 1}. {SURVEY_QUESTIONS[i]['soru']} -> {SURVEY_QUESTIONS[i]['secenekler'][a]}"
         for i, a in enumerate(answers)]
    return json.dumps({"cevaplar": answers, "detay": q}, ensure_ascii=False)


# =============================================================================
# 4) AJANLAR
# =============================================================================

class MarketAnalystAgent:
    """Piyasa Analisti: piyasa görünümünü ve risk iştahını üretir."""

    name = "Piyasa Analisti"

    def __init__(self, llm: BaseChatModel):
        self.llm = llm

    def run(self) -> Dict[str, Any]:
        snap = compute_market_snapshot()
        lines = []
        for key, o in snap["outlook"].items():
            lines.append(
                f"{key}: {o['ad']} | momentum %{o['momentum'] * 100:.1f} | "
                f"vol. %{o['volatilite'] * 100:.1f} | görünüm {o['gorunum']}"
            )
        data_str = "\n".join(lines)
        rejim = snap["rejim"]
        narrative = self.llm.invoke(
            f"Piyasa görünümünü özetle:\n{data_str}\nPiyasa rejimi: {rejim}"
        ).content
        return {"market_snapshot": snap, "ozet": data_str,
                "anlatim": str(narrative)}


class RiskAssessorAgent:
    """Risk Değerlendiricisi: anketi puanlar, profili ve hedef dağılımı belirler."""

    name = "Risk Değerlendiricisi"

    def __init__(self, llm: BaseChatModel):
        self.llm = llm

    def run(self, answers: List[int]) -> Dict[str, Any]:
        score = score_survey(answers)
        profile = risk_profile_for_score(score)
        target = RISK_PROFILES[profile]["hedef"]
        max_equity = RISK_PROFILES[profile]["max_equity"]
        narrative = self.llm.invoke(
            f"Anket skoru {score}/20 -> {profile} profili. "
            f"Maks. hisse ağırlığı %{max_equity * 100:.0f}. Hedef dağılım: {target}"
        ).content
        return {"skor": score, "profil": profile, "hedef_agirlik": target,
                "max_hisse": max_equity, "anlatim": str(narrative)}


class PortfolioManagerAgent:
    """Portföy Yöneticisi: hedef vs mevcut sapmayı ölçer, alım/satım önerir."""

    name = "Portföy Yöneticisi"

    def __init__(self, llm: BaseChatModel):
        self.llm = llm

    def run(self, outlook: Dict[str, Any], risk: Dict[str, Any],
            holdings: Dict[str, float]) -> Dict[str, Any]:
        target = dict(risk["hedef_agirlik"])
        rejim = outlook["rejim"]
        # Risk-on ise hisse ağırlığını yukarı tilt et (maks. profilin tavanında)
        if rejim == "risk-on":
            uptilt = min(target.get("hisse", 0.0) + 0.05, risk["max_hisse"])
            target["hisse"] = uptilt

        # Mevcut fon değerleri ve toplam
        values = {k: holdings[k] * outlook["outlook"][k]["son_fiyat"]
                  for k in holdings if k in outlook["outlook"]}
        total = sum(values.values())
        current = {}
        for k, v in values.items():
            cls = FUNDS[k]["urun_sinifi"]
            current.setdefault(cls, 0.0)
            current[cls] += v

        # Hedef TL (sınıf bazında) -> fon bazında orantılı dağıtım
        target_by_class: Dict[str, float] = {}
        for cls, w in target.items():
            target_by_class[cls] = w * total

        # Fon bazında hedef = sınıf hedefi içinde o sınıftaki mevcut payına göre
        class_pool: Dict[str, float] = {}
        class_total: Dict[str, float] = {}
        for k, v in values.items():
            cls = FUNDS[k]["urun_sinifi"]
            class_pool.setdefault(cls, 0.0)
            class_total.setdefault(cls, 0.0)
            class_pool[cls] += v
            class_total[cls] += v

        orders: Dict[str, Dict[str, Any]] = {}
        for k, v in values.items():
            cls = FUNDS[k]["urun_sinifi"]
            cls_target_tl = target_by_class.get(cls, 0.0)
            if class_total[cls] > 0:
                share = v / class_total[cls]
            else:
                share = 0.0
            fund_target = cls_target_tl * share
            drift = (fund_target - v) / total if total > 0 else 0.0
            delta = fund_target - v
            action = "tut"
            if drift >= 0.02:
                action = "al"
            elif drift <= -0.02:
                action = "sat"
            orders[k] = {
                "ad": FUNDS[k]["ad"],
                "mevcut": v,
                "hedef": fund_target,
                "delta": delta,
                "sapma": drift,
                "islem": action,
            }

        report = {
            "toplam": total,
            "hedef_agirlik": target,
            "rejim": rejim,
            "emirler": orders,
            "aktif_emir_sayisi": sum(1 for o in orders.values() if o["islem"] != "tut"),
        }
        emir_metni = "\n".join(
            f"{k}: {o['islem']} ({o['delta']:+.0f} TL, sapma %{o['sapma'] * 100:.1f})"
            for k, o in orders.items()
        )
        narrative = self.llm.invoke(
            f"Toplam portföy {total:.0f} TL. Rejim {rejim}. Emirler:\n{emir_metni}"
        ).content
        report["anlatim"] = str(narrative)
        return report


# =============================================================================
# 5) SAHTE LLM (LangChain FakeListChatModel) + SELF-TEST
# =============================================================================

def build_mock_llm() -> FakeListChatModel:
    """Deterministik, çevrimdışı sahte LLM."""
    return FakeListChatModel(responses=[
        "Rapor deterministik hesaplamalara dayalıdır (çevrimdışı simülasyon)."
    ])


if __name__ == "__main__":
    llm = build_mock_llm()

    piyasa = MarketAnalystAgent(llm).run()
    print("== Piyasa Analisti ==")
    print(piyasa["ozet"])
    snap = piyasa["market_snapshot"]
    print("Rejim:", snap["rejim"], "| Ort. vol %{:.1f}".format(snap["ort_volatilite"] * 100))

    cevaplar = [3, 3, 2, 3, 2]
    risk = RiskAssessorAgent(llm).run(cevaplar)
    print("\n== Risk Değerlendiricisi ==")
    print("Skor:", risk["skor"], "| Profil:", risk["profil"])
    print("Hedef dağılım:", risk["hedef_agirlik"])

    mevcut = {"TTA": 100.0, "TDB": 200.0, "TKA": 150.0,
              "TBA": 300.0, "TAL": 120.0, "TUV": 80.0}
    portfoy = PortfolioManagerAgent(llm).run(piyasa["market_snapshot"], risk, mevcut)
    print("\n== Portföy Yöneticisi ==")
    print("Toplam:", round(portfoy["toplam"], 2), "TL")
    for k, o in portfoy["emirler"].items():
        print(f"  {k}: {o['islem']} (delta {o['delta']:+.2f} TL)")
    print("Aktif emir:", portfoy["aktif_emir_sayisi"])