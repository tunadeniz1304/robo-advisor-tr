# -*- coding: utf-8 -*-
"""
Otonom Finansal Danışman (Robo-Advisor) - Ana Orkestrasyon
==========================================================
Çoklu ajan akışı:
    Piyasa Analisti -> Risk Değerlendiricisi -> Portföy Yöneticisi
Tüm matematik deterministik (saf Python); doğal dil gerekçeleri LangChain'in
çevrimdışı sahte LLM katmanından gelir. Kod çevrimdışı, tekrarlanabilir şekilde çalışır.
"""

from __future__ import annotations

import sys

from agent import (FakeListChatModel, MarketAnalystAgent, RiskAssessorAgent,
                   PortfolioManagerAgent)


def main() -> None:
    # Windows tr-TR konsolunda Türkçe karakterlerin bozulmasını önler.
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if sys.stderr and hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    # Deterministik, çevrimdışı sahte LLM.
    llm = FakeListChatModel(responses=[
        "Rapor deterministik hesaplamalara dayalıdır (çevrimdışı simülasyon)."
    ])

    print("=" * 62)
    print("  OTONOM FİNANSAL DANIŞMAN (Robo-Advisor)")
    print("  LangChain tabanlı çoklu ajan orkestrasyonu")
    print("=" * 62)

    # ---- 1) Piyasa Analisti ----
    print("\n[1] PİYASA ANALİSTİ — piyasa görünümü ve risk iştahı")
    piyasa = MarketAnalystAgent(llm).run()
    snap = piyasa["market_snapshot"]
    for key, o in snap["outlook"].items():
        print(f"  {key:<3} {o['ad']:<38} mom %{o['momentum']*100:5.1f} "
              f"vol %{o['volatilite']*100:4.1f} görünüm: {o['gorunum']}")
    print(f"  → Piyasa rejimi: {snap['rejim']} | "
          f"ort. volatilite %{snap['ort_volatilite']*100:.1f} | "
          f"ort. duyarlılık {snap['ort_duyarlilik']:+.2f}")
    print(f"  GPT-özeti: {piyasa['anlatim']}")

    # ---- 2) Risk Değerlendiricisi ----
    print("\n[2] RİSK DEĞERLENDİRİCİSİ — müşteri anketi")
    answers = [3, 3, 2, 3, 2]  # örnek senaryo: deneyimli, uzun vade, risk toleranslı
    risk = RiskAssessorAgent(llm).run(answers)
    print(f"  Anket skoru: {risk['skor']}/20 → Profil: {risk['profil']}")
    print("  Hedef varlık dağılımı:")
    for cls, w in risk["hedef_agirlik"].items():
        print(f"    - {cls:<13} %{w*100:.0f}")
    print(f"  Maks. hisse ağırlığı: %{risk['max_hisse']*100:.0f}")
    print(f"  GPT-özeti: {risk['anlatim']}")

    # ---- 3) Portföy Yöneticisi ----
    print("\n[3] PORTFÖY YÖNETİCİSİ — mevcut portföy vs hedef dağılım")
    mevcut = {  # fon -> adet
        "TTA": 100.0, "TDB": 200.0, "TKA": 150.0,
        "TBA": 300.0, "TAL": 120.0, "TUV": 80.0,
    }
    portfoy = PortfolioManagerAgent(llm).run(snap, risk, mevcut)
    print(f"  Toplam portföy değeri: {portfoy['toplam']:,.0f} TL")
    print("  Fon bazında kararlar:")
    for key, o in portfoy["emirler"].items():
        print(f"    {key:<3} {o['islem'].upper():<4} "
              f"delta {o['delta']:+8.0f} TL | sapma %{o['sapma']*100:+5.1f}")
    print(f"  Aktif (al/sat) emir sayısı: {portfoy['aktif_emir_sayisi']}")
    print(f"  GPT-özeti: {portfoy['anlatim']}")

    print("\n" + "=" * 62)
    print("  SÜREÇ TAMAMLANDI — tüm hesaplar deterministik, çevrimdışı.")
    print("=" * 62)


if __name__ == "__main__":
    main()
