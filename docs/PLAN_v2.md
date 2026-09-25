# Uygulama Planı v2 — denetim bulgularını kapatma

v1 sonrası bağımsız denetim projeye 7,5/10 verdi. Bu plan, denetimin 25 bulgusunu ve üç yeni
özelliği fazlara böler. Her bulgu önce **kırmızı** bir regresyon testiyle yeniden üretildi (V0),
sonra düzeltildi.

## V0 — bulguların doğrulanması

`pytest tests/test_v2_*.py` V0 sonunda: **52 kırmızı, 7 yeşil**. Yeşil kalanlar bugünkü doğru
davranışı sabitleyen kontrol testleridir (ör. yatırmanın TWR'ı etkilememesi, snapshot boyutu,
testlerde ağ yasağı, `dev-only` JWT sırrının prod'da reddi).

| # | Bulgu | Yeniden üreten test | V0 sonucu |
|---|---|---|---|
| A.1 | `/performance` ile `/report` çelişiyor, rf=0 | `test_v2_performance.py::test_performance_and_report_endpoints_agree` | kırmızı (farklı alanlar/değerler) |
| A.2 | Temettü dış giriş, ücret dış çıkış sayılıyor | `test_dividend_is_internal_return_not_external_flow`, `test_fee_reduces_twr` | kırmızı (TWR 0) |
| A.3 | Hafta sonu akışı kayboluyor | `test_saturday_deposit_rolls_forward_to_monday`, `test_flow_after_last_price_day_is_valued_on_last_day` | kırmızı |
| A.4 | TWR kümülatif / MWR yıllık etiketsiz | `test_report_labels_cumulative_and_annualized_metrics` | kırmızı |
| A.5 | Mutlak bant: ASELS %1,3→%7,6 "bant içinde" | `test_v2_bands.py::test_asels_scenario_triggers_rebalance` (+3) | kırmızı |
| A.6 | Backtest in-sample (look-ahead) | `test_v2_walkforward.py` (6 test) | kırmızı |
| A.7 | "Idzorek" aslında kapalı form kısayolu | `test_v2_optimizer.py::test_idzorek_*` | kırmızı |
| A.8 | Sınıf bantları optimizer'ı etkisizleştiriyor | `test_hrp_and_min_cvar_differ_in_high_vol_regime` | kırmızı |
| B.9 | Gerçek veri toplama scripti yok | `test_v2_data.py::test_fetch_tefas_fund_with_fake_http`, `test_build_snapshot_marks_proxies_and_sources` | kırmızı |
| B.10 | Veri kalitesi katmanı yok | `test_quality_*` | kırmızı |
| B.11 | Seri bazında metadata / `is_proxy` yok | `test_snapshot_meta_has_per_series_metadata` | kırmızı |
| C.13 | Prod'da PII anahtarı / JWT sırrı zayıf olabilir | `test_v2_security.py::test_prod_*` | kırmızı |
| C.14 | `/llm/status`, `/metrics` herkese açık | `test_llm_status_*`, `test_metrics_*` | kırmızı |
| C.15 | Tek süreç kilidi / rate limit | `test_redis_lock_*`, `test_rate_limit_shared_*`, `test_two_app_instances_*` | kırmızı |
| C.16 | JWT'deki rol eskiyor, 403/404 tutarsız | `test_demoted_*`, `test_foreign_resources_answer_404_not_403` | kırmızı |
| C.17 | mypy gevşek | `test_mypy_config_is_strict` | kırmızı |
| D.18 | `app.js` okunaksız | `test_v2_frontend_docs.py::test_frontend_is_split_into_modules`, `test_frontend_lines_are_readable` | kırmızı |
| D.19 | Erişilebilirlik eksik | `test_every_form_control_has_a_label_and_charts_have_tables`, `test_focus_ring_and_skip_link` | kırmızı |
| D.20 | UI E2E yok | `tests/e2e/test_ui_flow.py` (V5'te) | — |
| D.21 | Rozetler gerçek durumu yansıtmıyor | `test_v2_data.py::test_status_badges_reflect_reality` | kırmızı |
| E.22 | README eşitlik iddiası | `test_readme_is_honest` | kırmızı |
| E.23 | İç LLM adresi takip edilen dosyalarda | `test_internal_llm_host_is_not_in_tracked_files` | kırmızı |
| E.24 | Doğrulanmamış mevzuat atıfları | `test_regulatory_references_are_sourced_or_flagged` | kırmızı |
| E.25 | FINAL_REPORT_v2 | V7'de | — |
| B.12 | Önce / sonra sayıları | V3 sonunda `FINAL_REPORT_v2.md` | — |

## Fazlar

| Faz | İçerik | Çıkış |
|---|---|---|
| V1 | Tek ledger tabanlı performans motoru (`services/analytics/engine.py`), GIPS akış sınıflandırması, takvim günü roll-forward, açık metrik adları; `/performance` aynı motoru kullanır (deprecated başlığıyla) | performans testleri yeşil |
| V2 | Göreli+mutlak bant, walk-forward backtest + benchmark'lar, gerçek Idzorek Ω, sınıf içi söz hakkı | ASELS + look-ahead yeşil |
| V3 | `scripts/fetch_real_data.py`, `services/market_data/quality.py`, parquet snapshot + seri metadata'sı, `docs/DATA.md` | internetsiz testler yeşil |
| V4 | Prod sırları, `/metrics` ve `/llm/status` koruması, Redis kilit + rate limit, Postgres checkpointer, rol tazeleme, 404, mypy sıkılaştırma | çoklu instance testi yeşil |
| V5 | ES modülleri, erişilebilirlik, Playwright E2E, rozetler | E2E yeşil (veya CI'da) |
| V6 | Risk ayrıştırma/faktörler, vergi lotu motoru, model portföy walk-forward takibi | ilgili testler yeşil |
| V7 | README "Sınırlamalar", iç LLM adresinin temizliği, mevzuat doğrulaması, FINAL_REPORT_v2 | iç LLM adresi hiçbir takip edilen dosyada yok |
| V8 | Bağımsız denetim döngüsü (en fazla 3 tur) | ≥ 9/10 veya gerekçeli bulgular |

## Kasıtlı davranış değişiklikleri

* `GET /portfolios/{id}/performance` artık sabit ağırlıklı varsayımsal backtest değil, `/report`
  ile aynı ledger motorunu kullanır; yanıt alanları `twr_cumulative`, `twr_annualized`, `sharpe` …
  olarak yeniden adlandırılır ve `Deprecation` başlığı eklenir.
* `/report` yanıtındaki `twr` / `mwr` alanları `twr_cumulative` / `twr_annualized` /
  `mwr_annualized` + `period` ile değiştirilir.
* Başkasının kaynağına erişim 403 yerine 404 döner (kimlik tahmini engellenir). v1 testleri buna
  göre güncellenir.
* `/llm/status` kimlik doğrulama ister; `/metrics` varsayılan olarak kapalıdır
  (`METRICS_PUBLIC=true` veya `METRICS_TOKEN`).
