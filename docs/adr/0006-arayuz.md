# ADR 0006: Bağımlılıksız arayüz (vanilla JS + Chart.js)

- Durum: Kabul edildi
- Tarih: 2026-09-24

## Bağlam
Node build zinciri tek komutla ayağa kaldırmayı ve offline çalışmayı zorlaştırır.

## Karar
`frontend/` altında ES modülleri ve vendorlanmış Chart.js; FastAPI `/assets` altında servis eder. Katı CSP (`script-src 'self'`, eval ve inline script yok).

## Sonuçlar
Build adımı yok, Docker imajı sade; tasarım sistemi CSS değişkenleriyle koyu/açık tema.
