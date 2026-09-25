# Model portföyler: seviye karşılaştırması (walk-forward)

Bu dosya `scripts/model_portfolio_report.py` ile gerçek veri snapshot'ından üretilir (snapshot sonu: 2026-09-24).

* **Test dönemi:** 2021-11-23 → 2026-09-24 (her seviye için aynı).
* **Yöntem:** `hrp` optimizer, 756 işlem günlük tahmin penceresi, 63 günde bir yeniden optimizasyon; her tarihte yalnızca o tarihe kadarki veri kullanılır (look-ahead yok).
* **Statik model:** sınıf temsilcileriyle model ağırlıkları, bant politikasıyla rebalance.
* **Sharpe** dönem ortalaması TL politika faiziyle (%31.5) hesaplanır; işlem maliyetleri dahildir, vergi hariçtir.

| Seviye | Profil | WF CAGR | WF vol. | WF Sharpe | WF maks. düşüş | Statik CAGR | Statik vol. | Statik maks. düşüş |
|---|---|---|---|---|---|---|---|---|
| 1 | Çok Muhafazakâr | %38,4 | %2,0 | 2,52 | %-3,2 | %39,4 | %2,2 | %-3,0 |
| 2 | Muhafazakâr | %37,3 | %3,3 | 1,33 | %-8,3 | %40,1 | %3,3 | %-6,3 |
| 3 | Temkinli | %40,6 | %4,4 | 1,53 | %-10,3 | %42,4 | %5,2 | %-9,1 |
| 4 | Temkinli-Dengeli | %45,2 | %7,0 | 1,45 | %-11,6 | %44,2 | %7,1 | %-10,1 |
| 5 | Dengeli | %46,6 | %9,0 | 1,25 | %-12,5 | %47,7 | %9,5 | %-10,8 |
| 6 | Dengeli-Büyüme | %48,1 | %11,1 | 1,13 | %-13,4 | %49,4 | %11,9 | %-12,8 |
| 7 | Büyüme | %50,2 | %13,4 | 1,06 | %-14,4 | %52,8 | %15,0 | %-14,6 |
| 8 | Büyüme-Agresif | %51,4 | %15,6 | 0,98 | %-15,4 | %55,1 | %18,0 | %-16,7 |
| 9 | Agresif | %52,4 | %17,1 | 0,95 | %-15,8 | %59,3 | %21,6 | %-18,5 |
| 10 | Çok Agresif | %53,0 | %18,9 | 0,90 | %-16,3 | %61,1 | %25,4 | %-20,4 |

## Karşılaştırma ölçütleri (aynı dönem)

| Ölçüt | CAGR | Vol. | Maks. düşüş |
|---|---|---|---|
| XU100 | %49,3 | %28,5 | %-22,9 |
| 60/40 | %45,2 | %17,1 | %-13,4 |
| TUFE+3 | %52,5 | %2,5 | %0,0 |

## Okuma notları

* Bu test döneminde TÜFE+%3 hedefini walk-forward CAGR ile geçen seviye sayısı: **1/10**; XU100'ü geçen: **4/10** (daha düşük volatiliteyle).
* Yüksek enflasyon döneminde nominal TL getiriler yüksektir; asıl karşılaştırma TÜFE+3 ve 60/40 ölçütleriyle yapılmalıdır.
* Seviye arttıkça volatilite ve maksimum düşüşün artması beklenir; artmıyorsa model portföy bantları veya evren gözden geçirilmelidir.
* Geçmiş performans gelecek için gösterge değildir; bilgi amaçlıdır.
