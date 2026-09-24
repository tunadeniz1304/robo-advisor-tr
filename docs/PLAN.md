# Uygulama Planı — Otonom Finansal Danışman v2

Bu belge, projeyi kurumsal seviyede bir dijital varlık yönetimi platformuna
dönüştürme planını, F0 keşfinde doğrulanan varsayımları ve bilinçli sapmaları
kaydeder.

## F0 keşif bulguları (doğrulandı)

| Varsayım | Durum |
|---|---|
| Python 3.11, FastAPI, SQLAlchemy 2 async, LangGraph ≥1.0, pydantic v2 | Doğrulandı (LangGraph 1.2, FastAPI 0.14x) |
| 29 test, hepsi yeşil | Doğrulandı |
| Graf `market → risk → portfolio_manager` | Doğrulandı; graf her istekte derleniyor |
| §1'deki 14 hata | Hepsi kodda gözlendi (ör. `performance_metrics` `series.sum()`, drift `target_weights={}`, `hash(sym)` seed) |
| README / seed / migration yok | Doğrulandı |
| `.env` anahtar adları | `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` (yalnız adlar kontrol edildi) |
| İnternet erişimi | Var — offline snapshot gerçek Yahoo Finance verisinden üretildi |

## Mimari kararlar ve sapmalar

1. **Optimizasyon çekirdeği NumPy + SciPy ile yazıldı** (PyPortfolioOpt / cvxpy
   yerine). HRP, Black-Litterman (Idzorek güveni), min-CVaR (Rockafellar–Uryasev
   LP, `scipy.optimize.linprog`), risk paritesi ve kısıtlı ortalama-varyans
   kendi, test edilebilir implementasyonlardır; Ledoit-Wolf shrinkage kapalı
   formdadır. Minimum devir hızlı yeniden dengeleme de L1 amaçlı bir LP olarak
   `linprog` ile çözülür (cvxpy formülasyonuyla matematiksel olarak aynı).
   Gerekçe: Windows/Docker'da ağır derleme bağımlılığı yok, deterministik, ADR-001.
2. **Ön yüz: bağımlılıksız Alpine.js + Chart.js** (`frontend/`, vendored,
   build adımı yok). Gerekçe: tek komutla ayağa kalkma, offline çalışma, ADR-006.
3. **Veri**: `data/snapshots/` altında gzip'li CSV (parquet yerine — pyarrow
   çekirdek bağımlılığı olmasın). TÜFE ve politika faizi serileri EVDS anahtarı
   olmadığında elle derlenmiş **yaklaşık** aylık serilerdir; `EVDS_API_KEY`
   verilirse `scripts/refresh_snapshot.py` gerçek seriyi çeker.
4. **TEFAS fonları**: Para piyasası, TL tahvil fonu ve eurobond fonu için
   tarihsel seri yoksa **proxy** üretilir (politika faizi tahakkuku, süre
   modeli, EMB×USDTRY). Enstrüman tablosunda `source="proxy"` olarak işaretli.
5. **Rebalance artık iki aşamalı**: `POST /advisor/rebalance/{id}` bir öneri
   (ONAY_BEKLIYOR) üretir; yürütme yalnızca `POST /proposals/{id}/approve` ile.
   Eski "tek çağrıda yürüt" davranışını test eden testler bu akışa göre
   güncellendi.
6. **Hash-zincirli audit** (`audit_log`) mevcut `advisor_runs` tablosunun
   yanına eklendi; `advisor_runs` geriye dönük uyumluluk için korunur.
7. **LLM çıktıları** tek bir `LLMGateway` üzerinden geçer: canlı → doğrulama
   (pydantic + sayı koruması) → 1 onarım → deterministik demo fallback.

## Faz planı

| Faz | İçerik | Çıkış kriteri |
|---|---|---|
| F0 | pyproject (ruff, mypy, pytest-cov), Makefile, bu plan | 29 test yeşil, ruff temiz |
| F1 | LLM sözleşmesi, 14 hata düzeltmesi, güvenlik temeli (JWT, rate limit, CORS, başlıklar) | live/demo/fallback testleri, anahtarsız rebalance 200 |
| F2 | Alembic + Decimal para + yeni tablolar + veri katmanı + snapshot | internetsiz test paketi yeşil |
| F3 | SPK uygunluk testi + optimizasyon + model portföyler | optimizer/uygunluk testleri |
| F4 | Öneri→onay→yürütme, SimulatedBroker, LangGraph interrupt, hedef MC | E2E öneri akışı, MC testleri |
| F5 | Analitik/backtest/tear sheet + Prometheus/health | tear sheet testleri, `/metrics` |
| F6 | Açıklanabilirlik, rejim, BL görüşleri, stres, autopilot/dürtmeler | ilgili testler |
| F7 | Otomatik yorum + yetki zarflı copilot (SSE) | fake LLM tool-use testleri |
| F8 | Yeni arayüz + demo verisi | E2E smoke |
| F9 | README, docs, ADR, CI, CHANGELOG, v2.0.0 | tüm kalite kapıları |

## Kalite kapısı

`ruff check .`, `ruff format --check .`, `mypy .`, `pytest --cov` (yeni kodda
≥ %80), `docker compose build`.
