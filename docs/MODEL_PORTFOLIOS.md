# Model portföyler: seviye karşılaştırması (walk-forward)

Bu dosya `scripts/model_portfolio_report.py` ile gerçek veri snapshot'ından üretilir (snapshot sonu: 2026-09-25).

* **Test dönemi:** 2021-11-24 → 2026-09-25 (her seviye için aynı).
* **Yöntem:** `hrp` optimizer, 756 işlem günlük tahmin penceresi, 63 günde bir yeniden optimizasyon; her tarihte yalnızca o tarihe kadarki veri kullanılır (look-ahead yok).
* **Statik model:** model sınıf ağırlıkları sınıf sepetlerine eşit dağıtılır (hisse sınıfı = evrendeki 15 BIST hissesi, döviz = USD+EUR, fonlar = sınıf temsilcisi), bant politikasıyla rebalance. Hisse sepeti bugünün büyük şirketlerinden oluştuğu için **hayatta kalma yanlılığı** taşır; statik sonuçlar bu yüzden iyimserdir.
* **Sharpe** dönem ortalaması TL politika faiziyle (%31.5) hesaplanır; işlem maliyetleri dahildir, vergi hariçtir.

| Seviye | Profil | WF CAGR | WF vol. | WF Sharpe | WF maks. düşüş | Statik CAGR | Statik vol. | Statik maks. düşüş |
|---|---|---|---|---|---|---|---|---|
| 1 | Çok Muhafazakâr | %40,0 | %2,1 | 2,94 | %-3,5 | %40,7 | %2,1 | %-2,9 |
| 2 | Muhafazakâr | %38,6 | %3,1 | 1,74 | %-7,6 | %40,9 | %3,3 | %-6,7 |
| 3 | Temkinli | %41,7 | %4,5 | 1,69 | %-9,4 | %43,2 | %5,1 | %-8,8 |
| 4 | Temkinli-Dengeli | %44,8 | %6,9 | 1,44 | %-10,9 | %44,9 | %7,0 | %-10,1 |
| 5 | Dengeli | %46,9 | %9,3 | 1,23 | %-12,1 | %47,2 | %9,6 | %-12,4 |
| 6 | Dengeli-Büyüme | %48,7 | %11,6 | 1,12 | %-13,4 | %48,5 | %11,9 | %-13,9 |
| 7 | Büyüme | %51,1 | %14,5 | 1,04 | %-15,0 | %50,9 | %14,9 | %-15,7 |
| 8 | Büyüme-Agresif | %53,1 | %17,1 | 0,97 | %-16,2 | %52,9 | %17,5 | %-17,2 |
| 9 | Agresif | %54,8 | %19,2 | 0,95 | %-17,0 | %55,5 | %20,4 | %-18,1 |
| 10 | Çok Agresif | %55,9 | %21,7 | 0,90 | %-18,0 | %57,1 | %23,3 | %-19,2 |

## Karşılaştırma ölçütleri (aynı dönem)

| Ölçüt | CAGR | Vol. | Maks. düşüş |
|---|---|---|---|
| XU100 | %50,1 | %29,0 | %-22,9 |
| 60/40 | %46,4 | %17,4 | %-13,4 |
| TUFE+3 | %54,7 | %2,6 | %0,0 |

## Okuma notları

* Bu test döneminde TÜFE+%3 hedefini walk-forward CAGR ile geçen seviye sayısı: **2/10**; XU100'ü geçen: **4/10** (daha düşük volatiliteyle).
* Yüksek enflasyon döneminde nominal TL getiriler yüksektir; asıl karşılaştırma TÜFE+3 ve 60/40 ölçütleriyle yapılmalıdır.
* Seviye arttıkça volatilite ve maksimum düşüşün artması beklenir; artmıyorsa model portföy bantları veya evren gözden geçirilmelidir.
* Geçmiş performans gelecek için gösterge değildir; bilgi amaçlıdır.
