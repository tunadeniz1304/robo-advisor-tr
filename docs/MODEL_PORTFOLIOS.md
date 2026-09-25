# Model portföyler: seviye karşılaştırması (walk-forward)

Bu dosya `scripts/model_portfolio_report.py` ile gerçek veri snapshot'ından üretilir (snapshot sonu: 2026-09-25).

* **Test dönemi:** 2021-09-24 → 2026-09-25 (her seviye için aynı).
* **Yöntem:** `hrp` optimizer, 756 işlem günlük tahmin penceresi, 63 günde bir yeniden optimizasyon; her tarihte yalnızca o tarihe kadarki veri kullanılır (look-ahead yok).
* **Statik model:** model sınıf ağırlıkları sınıf sepetlerine eşit dağıtılır (hisse sınıfı = evrendeki 15 BIST hissesi, döviz = USD+EUR, fonlar = sınıf temsilcisi), bant politikasıyla rebalance. Hisse sepeti bugünün büyük şirketlerinden oluştuğu için **hayatta kalma yanlılığı** taşır; statik sonuçlar bu yüzden iyimserdir. Walk-forward de aynı hisse evreninden seçim yaptığı için bu yanlılığı taşır.
* **Walk-forward evreni:** tüm dönem boyunca verisi eksiksiz olan semboller; ilk yeniden optimizasyonların tahmin pencereleri 2021-09 öncesi vekil fon verisi içerir (bkz. DATA).
* **Sharpe** risksiz getiri olarak dönemin TL politika faizlerinin günlük bileşik karşılığını kullanır (%36.3); işlem maliyetleri dahildir, vergi hariçtir.

| Seviye | Profil | WF CAGR | WF vol. | WF Sharpe | WF maks. düşüş | Statik CAGR | Statik vol. | Statik maks. düşüş |
|---|---|---|---|---|---|---|---|---|
| 1 | Çok Muhafazakâr | %40,1 | %2,3 | 1,17 | %-4,2 | %40,8 | %2,2 | %-3,0 |
| 2 | Muhafazakâr | %39,7 | %3,7 | 0,67 | %-8,9 | %42,1 | %3,4 | %-6,7 |
| 3 | Temkinli | %44,0 | %5,1 | 1,10 | %-10,9 | %45,1 | %5,3 | %-9,2 |
| 4 | Temkinli-Dengeli | %47,8 | %7,3 | 1,15 | %-12,5 | %48,2 | %7,1 | %-10,1 |
| 5 | Dengeli | %50,6 | %9,5 | 1,09 | %-13,5 | %51,3 | %9,6 | %-12,4 |
| 6 | Dengeli-Büyüme | %53,0 | %11,6 | 1,05 | %-14,5 | %53,3 | %11,9 | %-13,9 |
| 7 | Büyüme | %56,1 | %14,3 | 1,02 | %-15,9 | %56,6 | %14,9 | %-15,7 |
| 8 | Büyüme-Agresif | %58,7 | %16,9 | 0,98 | %-17,0 | %59,3 | %17,5 | %-17,1 |
| 9 | Agresif | %61,0 | %18,8 | 0,98 | %-17,7 | %62,6 | %20,3 | %-18,4 |
| 10 | Çok Agresif | %62,5 | %21,3 | 0,94 | %-18,6 | %64,6 | %23,1 | %-19,4 |

## Karşılaştırma ölçütleri (aynı dönem)

| Ölçüt | CAGR | Vol. | Maks. düşüş |
|---|---|---|---|
| XU100 | %56,4 | %28,7 | %-22,9 |
| 60/40 | %49,8 | %17,3 | %-13,4 |
| TUFE+3 | %54,3 | %2,5 | %0,0 |

## Okuma notları

* Bu test döneminde TÜFE+%3 hedefini walk-forward CAGR ile geçen seviye sayısı: **4/10**; XU100'ü geçen: **3/10** (daha düşük volatiliteyle).
* Yüksek enflasyon döneminde nominal TL getiriler yüksektir; asıl karşılaştırma TÜFE+3 ve 60/40 ölçütleriyle yapılmalıdır.
* Seviye arttıkça volatilite ve maksimum düşüşün artması beklenir; artmıyorsa model portföy bantları veya evren gözden geçirilmelidir.
* Geçmiş performans gelecek için gösterge değildir; bilgi amaçlıdır.
