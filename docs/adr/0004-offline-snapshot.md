# ADR 0004: Offline veri snapshot'ı

- Durum: Kabul edildi
- Tarih: 2026-09-24

## Bağlam
Canlı veri kaynakları kesilebilir; testler ağa çıkmamalı ve deterministik olmalı.

## Karar
`data/snapshots` altında ~10 yıllık günlük fiyat (gzip CSV, ~250 KB) ve aylık makro seriler repoda tutulur. Canlı Yahoo → snapshot zinciri, sembol bazında düşer ve UI rozetinde gösterilir. TEFAS benzeri ürünler proxy olarak türetilir; TÜFE/faiz EVDS anahtarı yoksa yaklaşıktır.

## Sonuçlar
İnternetsiz çalışma ve test; testlerde soket koruması ağ erişimini engeller.
