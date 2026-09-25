# ADR 0004: Offline veri snapshot'ı

- Durum: Kabul edildi
- Tarih: 2026-09-24

## Bağlam
Canlı veri kaynakları kesilebilir; testler ağa çıkmamalı ve deterministik olmalı.

## Karar
`data/snapshots` altında ~10 yıllık günlük fiyat (gzip CSV, ~250 KB) ve aylık makro seriler repoda tutulur. Canlı Yahoo → snapshot zinciri, sembol bazında düşer ve UI rozetinde gösterilir. TEFAS benzeri ürünler proxy olarak türetilir; TÜFE/faiz EVDS anahtarı yoksa yaklaşıktır.

## Sonuçlar
İnternetsiz çalışma ve test; testlerde soket koruması ağ erişimini engeller.

## Güncelleme (v2, 2026-09-25)
* Snapshot artık **gerçek kaynaklardan** üretilir (`scripts/fetch_real_data.py`): Yahoo Finance,
  TEFAS fon fiyatları, TCMB TÜFE ve politika faizi. Vekil yalnız TEFAS'ın 5 yıllık sınırından
  önceki fon geçmişi için kalır ve seri metadata'sında `proxy_until` ile işaretlenir.
* Biçim gzip CSV'den **parquet**'e (zstd, ~0,5 MB) geçti; `pyarrow` çekirdek bağımlılık oldu.
  Eski `prices.csv.gz` okunabilir (geri uyumluluk).
* Seri bazında `source`, `fetched_at`, `first_date`, `last_date`, `rows`, `is_proxy` metadata'sı ve
  veri kalitesi denetimi (`services/market_data/quality.py`) eklendi. Ayrıntı: `docs/DATA.md`.
