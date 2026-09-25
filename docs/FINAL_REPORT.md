# Final rapor — Bitiş tanımı kanıtları

Tarih: 2026-09-24 · Sürüm: v2.0.0

| # | Kriter | Kanıt |
|---|---|---|
| 1 | `docker compose up --build` → sağlıklı; giriş → anket → seviye → hedef → öneri → önizleme → onay → yürütme | Compose (PostgreSQL 16 + API) `healthy`; Alembic `0001` Postgres'te uygulandı. `scripts/e2e_smoke.py --base http://localhost:8010` tüm adımlarda `OK` (öneri `YURUTULDU`, 8 işlem). Tarayıcıda panel ve danışman onay kuyruğu doğrulandı (`docs/img/`). Aynı akış `tests/test_e2e.py` ile otomatik test edilir. |
| 2 | `.env` yokken DEMO; varken `llm_mode=live model=deepseek-v4-flash`, smoke OK, anahtar görünmez | Konteyner logu: `{"llm_mode": "live", "model": "deepseek-v4-flash", "host": "<LLM_BASE_URL host>", "event": "llm_startup"}`. `python scripts/llm_smoke.py` → `OK model=deepseek/deepseek-v4-flash latency=…ms`. Anahtarsız akışlar demo modunda test edilir (`test_rebalance_without_key_runs_in_demo_mode`, copilot/yorum testleri). Anahtarın log/yanıt/durum uç noktasında görünmediği `test_error_messages_never_contain_key`, `test_llm_status_live_has_no_key`, `test_startup_log_line` ile doğrulanır. |
| 3 | İnternet kapalıyken snapshot ile çalışma, testler yeşil | Test oturumu soket korumasıyla ağa çıkışı engeller (`test_network_is_blocked_in_tests`); tüm veri `data/snapshots` üzerinden (DATA_MODE=snapshot). Canlı kaynak düşerse zincir snapshot'a geçer (`test_chain_falls_back_to_snapshot`). |
| 4 | MC başarı olasılığı, what-if, stres, backtest gerçek sayılarla | `test_goal_api_simulate_and_what_if`, `test_explain_regime_and_stress_api`, `test_report_and_backtest_api`; UI'da fan grafiği, stres P&L, backtest karşılaştırması. |
| 5 | §1'deki 14 hata düzeltildi ve testli | `tests/test_bugfixes.py` (#1–#7, #10–#14), `tests/test_drift.py` (#9), `tests/test_advisor.py::test_concurrent_double_approval_executes_once` (çift yürütme yok). #8: `test_rebalance_without_key_runs_in_demo_mode` + timeout/model env testleri. |
| 6 | ruff, mypy, pytest --cov (≥%80), test sayısı ≫ 29 | `ruff check` + `ruff format --check` temiz; `mypy .` → "no issues found"; **228 test**, toplam kapsam **%92**. |
| 7 | README + docs + CI | `README.md`, `docs/ARCHITECTURE.md`, `docs/METHODOLOGY.md`, `docs/COMPLIANCE.md`, `docs/DEMO_SCRIPT.md`, `docs/adr/` (6 ADR), `.github/workflows/ci.yml`, `CHANGELOG.md`, `LICENSE`. |
| 8b | Git kuralları | Her iş birimi ayrı Conventional Commit, `origin/main`'e push edildi; commit'lerde AI atfı yok; `.env` ve `Anil1.md` repoda değil (`.gitignore` / `.git/info/exclude`); her commit öncesi sır taraması 0. |

## Tarayıcı doğrulamasında bulunan ve düzeltilen hatalar
- Hisse ağırlıklı seviyelerde `XU100.IS` hem model temsilcisi hem kıyas olarak istenince yinelenen
  sütun → `/nudges` 500. Kök neden `MarketDataService.returns` içinde düzeltildi (regresyon testi).
- Fonlanmamış (≈0) başlangıç değerine bölünme TWR'yi patlatıyordu → düzeltildi (regresyon testi).

## CI
`.github/workflows/ci.yml` push edildi. Depo özel olduğundan ve ortamda `gh`/token olmadığından
Actions sonucu buradan okunamadı; iş akışındaki adımların tümü (ruff, format, mypy, pytest
`--cov-fail-under=80`, `docker compose build`) yerelde yeşil doğrulandı. Actions sekmesinden
kontrol edilmesi önerilir.

## Bilinen sınırlamalar
- TEFAS fonları, eurobond ve TL tahvil proxy serilerdir; TÜFE/politika faizi EVDS anahtarı yoksa
  yaklaşıktır (`scripts/refresh_snapshot.py` EVDS ile yeniler).
- Opsiyonel P2.2 (BIST-30 direkt endeksleme) uygulanmadı; P2.3 ESG yalnızca enstrüman üyeliği ve
  `esg_only` filtresi olarak mevcut.
- Rate limit ve portföy kilitleri süreç içidir (tek worker); çoklu worker için Redis gerekir.
