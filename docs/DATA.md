# Veri kaynakları, snapshot ve veri kalitesi

Uygulama internetsiz çalışır: fiyat ve makro seriler `data/snapshots/` altındaki **snapshot**'tan
okunur. Snapshot, `scripts/fetch_real_data.py` ile **gerçek kaynaklardan** üretilir ve depoda
tutulur (testler ağa çıkmaz). `DATA_MODE=auto|live` iken Yahoo Finance'ten canlı veri denenir;
canlı kaynağın vermediği seriler (TEFAS fonları) sembol bazında snapshot'tan tamamlanır.

```bash
python scripts/fetch_real_data.py              # tüm seriler (TÜFE API'si yavaş: ~5 dk)
python scripts/fetch_real_data.py --reuse-cpi  # TÜFE'yi mevcut snapshot'tan al
python scripts/snapshot_metrics.py             # snapshot üzerinde anahtar sayılar
```

## Seriler ve kaynakları

| Sembol | Seri | Kaynak | Not |
|---|---|---|---|
| `XU100.IS`, `XU030.IS` | BIST 100 / BIST 30 | Yahoo Finance (`yfinance`) | 10 yıl, günlük kapanış |
| 15 BIST hissesi (`THYAO.IS` …) | Hisse fiyatı | Yahoo Finance | `auto_adjust=True`: bölünme ve temettü düzeltmeli |
| `USDTRY`, `EURTRY` | Döviz kuru | Yahoo Finance (`USDTRY=X`, `EURTRY=X`) | |
| `ALTIN_TL` | Gram altın (TL) | Yahoo Finance `GC=F × USDTRY=X / 31,1035` | İki gerçek serinin birim dönüşümü; iç piyasa primi yok |
| `TL_PPF` | Para piyasası fonu | **TEFAS `TI1`** — İş Portföy Para Piyasası (TL) Fonu | Sınıfın en büyük fonu. 2021-09-24 öncesi vekil (aşağıda) |
| `TL_TAHVIL` | Kısa vadeli borçlanma araçları fonu | **TEFAS `TZV`** — Ziraat Portföy Kısa Vadeli Borçlanma Araçları (TL) | 2021-09-24 öncesi vekil |
| `YOT` | Borçlanma araçları fonu | **TEFAS `YOT`** — Yapı Kredi Portföy Borçlanma Araçları | Daha uzun vadeli TL tahvil alternatifi; yalnız son 5 yıl |
| `EUROBOND_TL` | Eurobond fonu (USD) | **TEFAS `YBE`** — Yapı Kredi Portföy Eurobond (Dolar) Borçlanma Araçları | 2021-09-24 öncesi vekil |
| `IPV` | Eurobond fonu | **TEFAS `IPV`** — İş Portföy Eurobond Borçlanma Araçları (Döviz) | Yalnız son 5 yıl |
| `TTA` | Altın fonu | **TEFAS `TTA`** — İş Portföy Altın Fonu | Yalnız son 5 yıl |
| `TUFE` (makro) | TÜFE, 2003=100, aylık | **TCMB enflasyon hesaplayıcı API'si** (`appg.tcmb.gov.tr/KIMENFH/enflasyon/hesapla`), veri TÜİK'indir | Aylık değişimler TCMB'nin yayımladığı aylık TÜFE %'leriyle örtüşür |
| `POLICY_RATE` (makro) | TCMB etkin fonlama faizi | **TCMB faiz tabloları** (Merkez Bankası Faiz Oranları → 1 Hafta Repo ve Geç Likidite Penceresi) | Bir hafta repo; Ocak 2017 – Mayıs 2018 arası geç likidite penceresi borç verme oranı (aşağıda). Ay sonu değeri |
| `USDTRY` (makro) | Ay sonu kur | Yahoo Finance | |

### Fon seçimi gerekçesi
Her sınıf için hâlâ faal, büyük (portföy büyüklüğü ve yatırımcı sayısı yüksek) ve son 5 yılı tam
olan fonlar seçildi. Sınıf temsilcisi (model portföyde kullanılan) fon her sınıftan biridir; ikinci
fonlar (`YOT`, `IPV`, `TTA`) optimizer'a **sınıf içi** gerçek bir seçim alanı verir.

### TEFAS 5 yıl sınırı ve vekil (proxy) dönemleri
TEFAS'ın 2026'da yenilenen API'si (`/api/funds/fonFiyatBilgiGetir`) en fazla **60 ay** geriye
fiyat verir (eski `BindHistoryInfo` uç noktası kapatıldı). 10 yıllık backtest, stres ve rejim
analizleri için sınıf temsilcilerinin 2021-09-24 öncesi kısmı **belgelenmiş vekille** eklenir ve
ilk gerçek güne ölçeklenir (seviye sürekliliği; getiriler vekilindir):

| Sembol | Vekil yöntemi (`services/market_data/derive.py`) |
|---|---|
| `TL_PPF` | Politika faizinin günlük tahakkuku − yönetim ücreti |
| `TL_TAHVIL` | Faiz taşıma getirisi − 2 yıl süre × faiz değişimi |
| `EUROBOND_TL` | EMB (ABD'de işlem gören gelişen piyasa USD tahvil ETF'i) × USDTRY |

`data/snapshots/meta.json` içinde her seri için `source`, `fetched_at`, `first_date`, `last_date`,
`rows`, **`is_proxy`**, **`has_proxy_segment`** ve **`proxy_until`** alanları bulunur. `has_proxy_segment=true` serinin en az bir kısmının vekil olduğunu söyler; `is_proxy=true` serinin tamamen vekil
olduğunu (TEFAS'a ulaşılamadığında), `proxy_until` ise o tarihe kadarki kısmın vekil olduğunu
gösterir. Arayüzdeki veri rozeti ve "Veri kalitesi" kartı bu alanları gösterir.

Walk-forward backtest'in varsayılan 5 yıllık test dönemi ve optimizer'ın tahmin penceresi
(son ≤5 yıl) tamamen gerçek fon fiyatlarına düşer; vekil dönem yalnızca daha eski tarihsel
senaryolarda (2018 kur şoku, 2020 COVID) ve 10 yıllık analizlerde kullanılır.

### Bilinen sınırlamalar
* **Politika faizi 2017 – 2018 ortası:** TCMB Ocak 2017 – Mayıs 2018 arasında piyasayı bir hafta
  repo yerine geç likidite penceresinden (GLP) fonladı; 1 Haziran 2018'de bir hafta repo yeniden
  politika faizi oldu. Bu dönem için etkin fonlama faizi olarak **GLP borç verme oranı** kullanılır
  (`effective_funding_rate`). Yalnız repo kullanıldığında TL tahvil vekili 2 Temmuz 2018'de −%19,4'lük
  yapay bir sıçrama gösteriyordu; veri kalitesi katmanı bunu yakaladı. Bu bir yaklaşımdır:
  gerçek ağırlıklı ortalama fonlama maliyeti (AOFM) ancak `EVDS_API_KEY` ile (`TP.APIFON4`) alınır.
* **Mevduat faizi:** anahtarsız erişilebilen bir tarihsel kaynak bulunamadı; kullanılmıyor.
* **TÜFE baz değişikliği:** bir basın kaynağına göre TÜİK 2026 başında yeni baza geçti (doğrulanmadı); hesaplayıcı API'si 2003=100 bazında
  kesintisiz seri döndürüyor (zincirleme varsayımı doğrulanmadı).
* **Son çare:** TCMB kaynaklarına ulaşılamazsa depodaki `macro_manual.csv` (elle derlenmiş
  yaklaşık seri, `manual_approx`) kullanılır; bu durumda metadata'da `is_proxy=true` olur ve
  arayüz "Veri: yaklaşık" rozeti gösterir.
* Gram altın, iç piyasadaki makas/primi içermez.

## EVDS (opsiyonel)
`EVDS_API_KEY` ortam değişkeni varsa (anahtar `key` HTTP başlığında gönderilir, loglanmaz) TÜFE
(`TP.FG.J0`) ve ağırlıklı ortalama fonlama maliyeti (`TP.APIFON4`) EVDS3'ten
(`evds3.tcmb.gov.tr/igmevdsms-dis`) çekilir. Eski `evds2` servisi kapatıldı.

## Veri kalitesi katmanı
`services/market_data/quality.py` her seriyi denetler; eşikler `config/policy.toml` →
`[data_quality]`:

| Kontrol | Kural |
|---|---|
| `gap` | Ardışık iki gözlem arasında 7'den fazla eksik iş günü |
| `jump` | Günlük log getiri hem mutlak olarak > %15 hem de serinin robust (MAD) z-skoru > 8 |
| `split_suspect` | Sıçramanın fiyat oranı 1/k veya k'ya (k = 2…10) %3 içinde yakın — düzeltilmemiş bölünme/bedelsiz izi |
| `stale` | Son gözlem 7 günden eski (45 günden eskiyse hata) |

Sonuç `GET /api/v1/data/quality` ile döner ve arayüzde "Veri kalitesi" kartında gösterilir.
Son 90 günden (`recent_days`) eski uç hareketler `severity: info` olarak listelenir ve seri durumunu bozmaz; yeni olanlar ve bölünme şüphesi `warning`'dir. Vekiller politika faizini karar tarihinde değiştirir (ay sonu değeri kullanmak 13.09.2018 faiz artışını tahvil vekiline 1 Ekim'de yansıtıyordu). Gerçek piyasa olayları da (ör. Ağustos 2018 ve Aralık 2021 kur şokları) `jump` olarak
işaretlenebilir; bunlar hata değil, incelenmesi gereken uç gözlemlerdir.

## Lisans ve atıf
Bu depo verileri yeniden dağıtım lisansıyla değil, **bilgi amaçlı bir prototipin çevrimdışı
çalışabilmesi için** önbellek olarak içerir. Ticari kullanım için her kaynağın koşulları ayrıca
değerlendirilmelidir.

* **TEFAS / Takasbank** — "Kaynak: TEFAS". Kullanım koşulları verinin doğruluğunu garanti etmez
  ve yatırım tavsiyesi olmadığını belirtir (https://www.tefas.gov.tr/KullanimKosullari.aspx).
  Açık bir veri lisansı bulunamadı; istekler seyreltilir ve sonuç önbelleğe alınır.
* **TCMB** — "Kaynak: TCMB" (faiz tablosu, enflasyon hesaplayıcı). **TÜFE verisi TÜİK'indir:**
  "Kaynak: TÜİK (TCMB aracılığıyla)".
* **Yahoo Finance** — `yfinance` üzerinden; Yahoo'nun kullanım koşulları geçerlidir, veriler
  kişisel/araştırma amaçlı kullanıma uygundur ve ticari yeniden dağıtım için lisanslı değildir.

## Snapshot biçimi
* `prices.parquet` — tarih × enstrüman kapanış paneli (zstd sıkıştırmalı, < 1 MB).
* `macro.csv` — aylık TÜFE, politika faizi, USDTRY (ilk satırda kaynak açıklaması).
* `macro_manual.csv` — son çare yaklaşık seri (yalnız kaynaklara ulaşılamazsa).
* `meta.json` — üretim zamanı, seri bazında kaynak/tarih/satır/vekil bilgisi.
