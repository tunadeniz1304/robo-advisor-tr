# Final rapor v2 — denetim bulgularının kapatılması

v1 sonrası bağımsız denetim projeye **CV projesi olarak 7,5/10** vermişti. Bu rapor, 25 bulgunun her
birinin hangi commit(ler)le kapatıldığını, hangi regresyon testiyle korunduğunu, gerçek veriye
geçişin sayılara etkisini ve v2 sonundaki bağımsız denetim turlarını listeler.
Plan ve V0'da yeniden üretilen bulgular: [PLAN_v2](PLAN_v2.md).

## Kalite kapısı (v2.1.0)

| Kontrol | Sonuç |
|---|---|
| `ruff check .` / `ruff format --check .` | temiz |
| `mypy .` (`check_untyped_defs=true`; `services`, `core`, `llm` için `disallow_untyped_defs=true`) | temiz, 158 dosya |
| `pytest -q --cov` (E2E hariç) | **331 test geçti** (v1: 228); toplam kapsam **%94**, her modül ≥ %80 |
| `pytest tests/e2e` (Playwright, gerçek Chromium) | 3 akış testi geçti; CI'da Chromium kurulur |
| `docker compose build` | başarılı (api + PostgreSQL + Redis) |
| `git grep` ile iç LLM adresi | takip edilen dosyalarda yok |

## Bulgular → commit → test

Commit kısaltmaları `git log` ile doğrulanabilir. Her bulgu önce V0 commit'lerinde (b514a25,
e4edf6b, 3e63c95, bf70c50) kırmızı bir testle yeniden üretildi.

| # | Bulgu | Düzeltme commit'leri | Regresyon testleri |
|---|---|---|---|
| A.1 | `/performance` ile `/report` çelişkisi, rf=0 | 9704b1c, bffd8b8, 9eba07d, b1f3b6a | `tests/test_v2_performance.py::test_performance_and_report_endpoints_agree` |
| A.2 | Temettü/ücret yanlış sınıflandırılıyor | 9704b1c, ba765e0 | `test_dividend_is_internal_return_not_external_flow`, `test_fee_reduces_twr`, `test_cash_flow_api_updates_cash_and_classifies`, `test_cash_correction_is_recorded_as_external_flow` |
| A.3 | Hafta sonu akışları kayboluyor | 9704b1c | `test_saturday_deposit_rolls_forward_to_monday`, `test_flow_after_last_price_day_is_valued_on_last_day`, `test_weekend_trade_is_replayed`, `test_valuation_day_rolls_forward_and_clamps` |
| A.4 | Kümülatif/yıllık etiketsiz | 9eba07d, bffd8b8, 71667c5 | `test_report_labels_cumulative_and_annualized_metrics`, `test_summary_does_not_annualize_short_periods`, `test_summary_annualizes_multi_year_consistently` |
| A.5 | Mutlak bant (ASELS) | 789d72d, f33d45a | `tests/test_v2_bands.py::test_asels_scenario_triggers_rebalance`, `test_asels_scenario_plan_sells_asels`, `test_band_formula_relative_with_floor_and_cap`, `test_class_level_absolute_band` |
| A.6 | Backtest'te look-ahead | f92610f, b794c20, 9fc1ea1 | `tests/test_v2_walkforward.py::test_optimizer_never_sees_future_data`, `test_changing_future_prices_does_not_change_past_weights`, `test_walk_forward_reports_benchmarks`, `test_backtest_api_uses_walk_forward_for_optimizer` |
| A.7 | "Idzorek" kısayolu | 0ff5c51, 2d8d1f6 | `tests/test_v2_optimizer.py::test_idzorek_omega_hits_target_tilt`, `test_idzorek_matches_pypfopt_closed_form_for_multiple_views` (PyPortfolioOpt ile) |
| A.8 | Sınıf bantları optimizer'ı etkisizleştiriyor | 9fc1ea1, 73e0f5f, 2d8d1f6 | `test_hrp_and_min_cvar_differ_in_high_vol_regime`, `test_binding_constraints_are_reported` |
| B.9 | Gerçek veri toplama scripti | 3e2af67 | `tests/test_v2_data.py::test_fetch_tefas_fund_with_fake_http`, `test_fetch_tefas_retries_after_rate_limit`, `test_fetch_tcmb_cpi_pairs_rate_limit_and_unpublished_month`, `test_parse_tcmb_policy_rate_table`, `test_effective_funding_rate_uses_llw_in_2017_2018`, `test_splice_scales_proxy_to_real_start` |
| B.10 | Veri kalitesi katmanı | d13692a | `test_quality_checks_detect_gaps_jumps_and_staleness`, `test_quality_clean_series_is_ok`, `test_quality_detects_forward_filled_hole`, `test_quality_endpoint` |
| B.11 | Snapshot metadata, boyut, vekil işaretleme | e6facb1, fbf37a6 | `test_snapshot_meta_has_per_series_metadata`, `test_snapshot_is_small`, `test_build_snapshot_marks_proxies_and_sources`, `tests/test_data_layer.py::test_snapshot_is_complete_and_offline` |
| B.12 | Önce / sonra sayıları | fbf37a6, 0744302 | aşağıdaki tablo (`scripts/snapshot_metrics.py`) |
| C.13 | Prod sırları | fa0a8ab | `tests/test_v2_security.py::test_prod_without_pii_key_refuses_to_start`, `test_prod_rejects_weak_jwt_secret`, `test_prod_with_strong_secrets_starts` |
| C.14 | `/llm/status`, `/metrics` açık | 15c72ac | `test_llm_status_requires_auth_and_hides_host_from_customers`, `test_metrics_private_by_default`, `test_metrics_token`, `test_metrics_public_flag` |
| C.15 | Tek süreç kilidi / rate limit | 0835bf2 | `test_redis_lock_excludes_across_instances`, `test_rate_limit_shared_across_instances_with_redis_storage`, `test_two_app_instances_execute_a_proposal_once`, `test_checkpointer_uses_postgres_when_database_is_postgres`, `test_forwarded_for_is_ignored_unless_trusted` |
| C.16 | Eskiyen rol, 403/404 | 9a95914, 916bf7b | `test_demoted_user_loses_staff_access_with_old_token`, `test_demoted_admin_cannot_approve_with_old_token`, `test_foreign_resources_answer_404_not_403`, `tests/test_v2_coverage.py::test_runs_and_transactions_are_scoped_and_foreign_ids_are_404` |
| C.17 | mypy gevşek | b59aa39 | `test_mypy_config_is_strict` + CI'da `mypy .` |
| D.18 | Okunaksız `app.js` | 623cd9e | `tests/test_v2_frontend_docs.py::test_frontend_is_split_into_modules`, `test_frontend_lines_are_readable` |
| D.19 | Erişilebilirlik | 396e263 | `test_every_form_control_has_a_label_and_charts_have_tables`, `test_focus_ring_and_skip_link` |
| D.20 | UI E2E | 67f189e | `tests/e2e/test_ui_flow.py` (müşteri akışı, tüm ekranlar, danışman) |
| D.21 | Rozetler | d13692a, 396e263 | `tests/test_v2_data.py::test_status_badges_reflect_reality` + E2E rozet kontrolü |
| E.22 | README eşitlik iddiası | c3d815c | `test_readme_is_honest` |
| E.23 | İç LLM adresi | be89d02, 3ee879a | `test_internal_llm_host_is_not_in_tracked_files`, `tests/test_llm_contract.py::test_defaults_and_timeout_merge` |
| E.24 | Doğrulanmamış mevzuat atıfları | 2245033 | `test_regulatory_references_are_sourced_or_flagged` |
| E.25 | Bu rapor | (bu commit) | — |

Denetim sırasında ortaya çıkan ek düzeltmeler: davranış açığı artık yıllık MWR − yıllık TWR
(9eba07d; eskiden yıllık MWR ile kümülatif TWR karşılaştırılıyordu), X-Forwarded-For yalnız güvenilen
vekil arkasında kullanılıyor (0835bf2), TL tahvil vekilindeki 2018 yapay sıçraması veri kalitesi
katmanınca yakalanıp GLP oranıyla düzeltildi (3e2af67), öneri onay düğmesindeki yarış durumu (623cd9e).

### Yeni katma değer (§2)
| Özellik | Commit | Testler |
|---|---|---|
| Risk ayrıştırma + faktör modeli (`/portfolios/{id}/risk-sources`) | fa3132c | `tests/test_v2_risk_decomposition.py` |
| Vergi lotu motoru (FIFO/HIFO, gerçekleşen K/Z, satış ve hasat simülasyonu) | dec7ccb, 1326be1 | `tests/test_v2_tax.py` |
| Model portföy walk-forward takibi (`docs/MODEL_PORTFOLIOS.md`, UI grafiği) | 8920737, 1a04532 | `tests/test_v2_model_tracking.py` |

## Önce / sonra: vekil veriden gerçek veriye

Aynı (v2) kodla, eski snapshot (yaklaşık TÜFE/faiz, sentetik fon vekilleri) ve yeni snapshot (Yahoo,
TEFAS, TCMB) karşılaştırması. Seviye 5, 5 yıl; `python scripts/snapshot_metrics.py --dir <snapshot>`.

| Ölçü | Önce (vekil) | Sonra (gerçek) |
|---|---|---|
| Makro kaynağı | `manual_approx` | TCMB faiz tablosu + TCMB enflasyon hesaplayıcı |
| Risksiz faiz (son) | %34,0 | %37,0 |
| Yıllık TÜFE (son) | %26,7 | %31,5 (yayımlanan %31,51) |
| Model L5 backtest CAGR / vol / Sharpe / MDD | %38,6 / %10,7 / 0,57 / −%13,4 | %46,2 / %9,5 / 1,16 / −%12,2 |
| HRP L5 beklenen getiri / vol | %47,3 / %10,3 | %48,3 / %8,4 |
| Walk-forward HRP L5 CAGR / Sharpe (2021-11 → 2026-09) | %37,6 / 0,50 | %46,6 / 1,25 |
| Ölçütler CAGR: XU100 / 60/40 / TÜFE+3 | %48,5 / %37,4 / %48,4 | %49,3 / %45,3 / %52,5 |
| Hedef MC başarı olasılığı (1 M TL reel, 10 yıl, 5 bin TL/ay) | %4,5 | %10,0 |
| Gerekli aylık katkı (%80 başarı) | 8.676 TL | 7.574 TL |
| Stres: USDTRY +%30 / BIST −%25 | +%8,5 / −%7,3 | +%8,4 / −%7,5 |
| Stres: Mart 2020 / Aralık 2021 | −%9,4 / +%32,3 | −%9,4 / +%30,2 |
| Rejim | stres (%99,9) | stres (%99,7) |

Yorum: gerçek para piyasası ve tahvil fonları vekillerden daha yüksek getiri ve daha düşük
oynaklık gösterdi (TL faizinin fon getirisine tam yansıması), bu yüzden Sharpe belirgin yükseldi.
Walk-forward sonucu TÜFE+%3 hedefinin altında kaldı; bu, README'deki "Sınırlamalar"da belirtilen
model riskinin somut örneğidir. v1 denetiminin bulduğu çelişki (aynı portföy için Sharpe 3,89 ↔
−0,70; toplam getiri %517 ↔ %88) tek motora geçişle ortadan kalktı.

## Bağımsız denetim döngüsü (V8)

Kurallar: alt-ajan bu dosyayı ve `Anil1v2.md`'yi okumaz, `.env` okumaz, takip edilen dosyaları
değiştirmez, yalnız kendi başlattığı süreci kapatır, ayrı port ve geçici DB kullanır.

| Tur | Puan | Özet |
|---|---|---|
| v1 denetimi | 7,5/10 | 25 bulgu (bu rapordaki tablo) |
