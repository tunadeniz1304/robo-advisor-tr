# Değişiklik günlüğü

## [2.1.0] — 2026-09-25
Bağımsız denetimin (7,5/10) 25 bulgusu kapatıldı; ayrıntı: `docs/FINAL_REPORT_v2.md`.
### Düzeltildi
- Tek ledger tabanlı performans motoru: `/performance` ve `/report` aynı sayıları verir; risksiz faiz her yerde TCMB faizi; temettü/ücret/vergi portföy içi, yalnız yatırma/çekme dış akış; hafta sonu/tatil akışları sonraki değerleme gününe taşınır; `twr_cumulative`, `twr_annualized`, `mwr_annualized`, `period` alanları (bir yıldan kısa dönem yıllıklandırılmaz).
- Göreli + mutlak drift bantları (`min(%5, max(%0,5; %25×hedef))`, sınıf bandı %3).
- Optimizer'lı backtest walk-forward (look-ahead yok) ve XU100 / TÜFE+3 / 60/40 ölçütleri.
- Black-Litterman Ω: Idzorek yöntemi görüş başına sayısal çözülür (PyPortfolioOpt ile doğrulanır).
- Güvenlik: prod'da zayıf JWT sırrı / eksik PII anahtarıyla başlamama, `/llm/status` kimlik ister, `/metrics` varsayılan kapalı, rol her istekte DB'den, yabancı kayıt 404, X-Forwarded-For yalnız güvenilen vekilde.
### Eklendi
- Gerçek veri: `scripts/fetch_real_data.py` (Yahoo Finance, TEFAS, TCMB), parquet snapshot, seri bazında kaynak/vekil metadata'sı, veri kalitesi katmanı ve `GET /data/quality`, `GET /system/status`, `docs/DATA.md`.
- Redis ile paylaşılan portföy/denetim kilidi ve rate limit, PostgreSQL LangGraph checkpointer, compose'a `redis`.
- Risk ayrıştırma ve faktör modeli, FIFO/HIFO vergi lotu motoru, 10 model portföyün walk-forward takibi (`docs/MODEL_PORTFOLIOS.md`).
- ES modülleriyle yeniden yazılmış erişilebilir arayüz, Playwright E2E testleri.
- Nakit akışı ledger uç noktası (`POST /portfolios/{id}/cash-flows`).
### Değişti
- `/performance` kullanımdan kalkıyor (`Deprecation` başlığı; `/report`'un özeti).
- Başkasının kaydına erişim 403 yerine 404 döner.
- mypy sıkılaştırıldı; yfinance 1.x, pyarrow, redis, langgraph-checkpoint-postgres bağımlılıkları.
- README: sektör kıyası yerine "İlham alınan desenler" ve "Sınırlamalar"; iç LLM adresi kaldırıldı.
### Kaldırıldı
- `scripts/refresh_snapshot.py`, kullanılmayan `YFinanceSource` ve kapatılmış EVDS2 istemcisi, eski sabit ağırlıklı performans ve mutlak bantlı drift fonksiyonları.

## [2.0.0] — 2026-09-24
### Eklendi
- LLM sözleşmesi: OpenAI-uyumlu istemci (DeepSeek V4 Flash @ Evren), anahtar/URL/model takma adları, `LLM_MODE=auto|live|demo`, deterministik demo, gateway (JSON şema, 1 onarım, fallback), sayı halüsinasyonu koruması, KVKK redaction, `llm_usage`, `/llm/status`, `scripts/llm_smoke.py`.
- JWT kimlik doğrulama (müşteri/danışman/yönetici), sahiplik kontrolleri, rate limit, CORS, güvenlik başlıkları, request-id, gizli hata yanıtları, Fernet PII şifreleme.
- Alembic migration'ları ve tam veri modeli (Decimal para, profiller, enstrümanlar, model portföyler, hedefler, öneriler, emir/dolum, nakit akışları, fiyat/makro, hash-zincirli audit, dürtmeler, BL görüşleri, Autopilot).
- Çoklu varlık evreni (23 enstrüman), canlı → offline snapshot veri zinciri, TL/USD/reel getiriler.
- SPK uygunluk testi ve uygunluk kapısı; 10 seviyeli model portföy kütüphanesi.
- HRP, Black-Litterman, min-CVaR, risk paritesi, kısıtlı MVO; Ledoit-Wolf, James-Stein.
- Min-devir LP rebalance, maliyet/stopaj modeli, SimulatedBroker, öneri→onay→yürütme ve LangGraph interrupt, zamanlayıcı.
- Blok bootstrap hedef Monte Carlo, what-if; tear sheet, TWR/MWR, backtest; rejim, stres lab, Autopilot, dürtmeler, açıklama kartları; otomatik yorum ve Copilot (SSE).
- Yeni SPA arayüz, demo verisi (3 persona + danışman), E2E testleri, dokümantasyon ve CI.
### Düzeltildi
- v1'deki 14 bilinen hata (çift rebalance, bileşik olmayan getiri, rf=0 tangency, drift stub, momentum projeksiyonu, manuel işlem, cache, timeout/503, test çelişkisi, hash tohumu, bağımlılık/ölü kod, Float para, güvenlik, checkpointer) — her biri regresyon testli.
### Değişti
- `POST /advisor/rebalance` artık emir yürütmez; onay bekleyen öneri döndürür.

## [1.0.0]
- İlk sürüm: LangGraph tabanlı üç ajanlı rebalance, CRUD API, basit panel.
