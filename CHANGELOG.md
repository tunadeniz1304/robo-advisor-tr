# Değişiklik günlüğü

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
