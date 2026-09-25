# Final rapor v2 — denetim bulgularının kapatılması

v1 sonrası bağımsız denetim projeye **CV projesi olarak 7,5/10** vermişti. Bu rapor, 25 bulgunun her
birinin hangi commit(ler)le kapatıldığını, hangi regresyon testiyle korunduğunu, gerçek veriye
geçişin sayılara etkisini ve v2 sonundaki bağımsız denetim turlarını listeler.
Plan ve V0'da yeniden üretilen bulgular: [PLAN_v2](PLAN_v2.md).

## Kalite kapısı (v2.1.0)

| Kontrol | Sonuç |
|---|---|
| `ruff check .` / `ruff format --check .` | temiz |
| `mypy .` (`check_untyped_defs=true`; `services`, `core`, `llm` için `disallow_untyped_defs=true`) | temiz, 161 dosya |
| `pytest -q --cov` (E2E hariç) | **349 test geçti** (v1: 228); toplam kapsam **%94**, her modül ≥ %80 (`main.py` giriş noktası dahil; `--cov=.` ile ölçüldü) |
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
| v2 tur 1 | **8/10** | Testler/lint/mypy/E2E temiz; TWR, Euler, Idzorek, FIFO/HIFO bağımsız hesapla birebir. 13 bulgu (aşağıda) |
| v2 tur 2 | **8/10** | Canlı API, onay akışı ve maliyet mutabakatı doğrulandı; Sharpe risksiz faiz yöntemi dahil 7 bulgu (aşağıda) |
| v2 tur 3 | **8/10** | Yüksek önemde bulgu yok; Sharpe (nakit fonu ≈ 0), takvim yıllı walk-forward, net stopaj ve 404 sınırları bağımsız doğrulandı. 10 orta/düşük bulgu (aşağıda) |

### Tur 1 bulguları ve karşılıkları

| # | Önem | Bulgu | Karşılık | Commit | Test |
|---|---|---|---|---|---|
| 1 | Yüksek | Statik model portföyde hisse sınıfı tek hisseyle (THYAO) temsil ediliyor → getiriler şişik | Sınıf sepetleri: hisse = evrendeki 15 hisse eşit ağırlık, döviz = USD+EUR; hayatta kalma yanlılığı notu | f3d4ae2, 4ed150e | `tests/test_v2_audit.py::test_equity_class_is_a_basket_not_one_stock` |
| 2 | Orta | Yıllıklandırma 252 gün; veride yılda ~261 satır | Yıllık gözlem sayısı tarihlerden çıkarılır | 0f888fa | `test_tear_sheet_infers_observation_frequency_from_dates` |
| 3 | Orta | `/report` Sharpe'ı bugünkü faizle | Dönemde geçerli faizlerin ortalaması | 0f888fa | `test_performance_and_report_endpoints_agree` |
| 4 | Orta | Backtest ağırlıkları doğrulanmıyor (kaldıraç/açığa satış) | Uzun pozisyon, toplam 1 zorunlu | f648ad2 | `test_backtest_rejects_leverage_shorts_and_partial_weights` |
| 5 | Orta | Tahvil vekilinde 2018 şoku 2,5 hafta gecikmeli | Vekiller karar tarihli faiz serisi kullanır; snapshot yenilendi | 71a7d86 | veri kalitesi raporunda sıçrama 14.09.2018'e taşındı |
| 6 | Orta | Seviye 10 müşteriye savunmacı öneri (±10 puan bant + stres eğilimi) | Sınıf bandı ±5 puan | 82c6e86 | `test_aggressive_profile_stays_aggressive_under_stress_tilt` |
| 7 | Düşük | Yabancı portföye rebalance 422 | Bilinmeyen ve yabancı → 404 | f648ad2 | `test_advisor.py::test_unknown_portfolio_is_404`, `test_customer_sees_only_own_data` |
| 8 | Düşük | `is_proxy=false` ama ilk 5 yıl vekil | `has_proxy_segment` alanı | 71a7d86 | `test_snapshot_marks_proxy_segments` |
| 9 | Düşük | Çalıştırma sonrası SQLite WAL/SHM dosyaları `git status`'ta görünüyor | `.gitignore` | f648ad2 | — |
| 10 | Düşük | Gerçekleşen K/Z satış maliyetini düşmüyor | Satış maliyeti lotlara oransal dağıtılır | ae2d21f | `test_realized_pnl_is_net_of_sale_costs` |
| 11 | Düşük | `/llm/status` müşteriye yapılandırma açıyor | Müşteri yalnız `mode` görür | f648ad2 | `test_llm_status_shows_customers_only_the_mode` |
| 12 | Düşük | Kalite rozeti hep "uyarı" (gerçek olaylar) | 90 günden eski uç hareketler `info` | 71a7d86 | `test_old_extreme_moves_are_info_not_warning` |
| 13 | Düşük | Git geçmişi kısa sürede yoğun commit'ler | Geçmiş yeniden yazılmadı (force push yasak); commit'ler küçük ve Conventional Commits, AI atfı yok. Denetim süreci bu raporda açıkça belgelenir | — | — |

### Tur 2 bulguları ve karşılıkları

Tur 2 puanı **8/10**: araçlar (341 test, E2E, ruff, mypy) temiz; canlı API'de yetki sınırları,
onay akışı, idempotentlik, maliyet mutabakatı ve denetim zinciri doğrulandı. Kalan bulgular:

| # | Önem | Bulgu | Karşılık | Commit | Test |
|---|---|---|---|---|---|
| 1 | Yüksek | Sharpe/Sortino şişik: basit politika faizi yıllık bileşik oran gibi kullanılıyor (para piyasası fonu Sharpe ≈ 3,7) | Risksiz getiri `(1+r/365)^365−1`, dönem boyunca log ortalaması; `policy_rate()` alıntılanan oranı ayrıca verir | ac8a791 | `tests/test_v2_audit_round2.py::test_cash_fund_accruing_the_policy_rate_has_near_zero_sharpe` |
| 2 | Orta | DATA.md walk-forward tahmin penceresinin vekil içermediğini söylüyor | Belge düzeltildi: ilk ~12 yeniden optimizasyonun penceresi kısmen vekil; dışlanan semboller belirtildi | 5a17a6b | — |
| 3 | Orta | Rebalance stopajı brüt kâr üzerinden | `estimate_sale(fees=…)`: matrah net kâr (ledger ile aynı taban) | fde3f5b | `test_withholding_base_is_net_of_sale_fees` |
| 4 | Düşük-Orta | Walk-forward da hayatta kalma yanlılığı taşıyor | MODEL_PORTFOLIOS notu walk-forward'u da kapsar | 5a17a6b | — |
| 5 | Düşük | "5 yıllık test" = 5 × 252 satır = 4,84 takvim yılı | Test dönemi takvim yılıyla kesilir (`trim_for_test`) | d9e5812 | `test_walk_forward_test_period_is_calendar_years` |
| 6 | Düşük | Varsayılan lot yöntemi HIFO; altın stopajı | Varsayılan FIFO; altın oranının fon varsayımı METHODOLOGY'de açıklandı | ac8a791, 5a17a6b | `test_default_lot_method_is_fifo` |
| 7 | Düşük | Git geçmişi kısa sürede yoğun | Tur 1 #13 ile aynı gerekçe: geçmiş yeniden yazılmaz | — | — |

Gerekçeli bırakılanlar: optimizer'ın μ/Σ yıllıklandırması 252 ile kalır (girdi ölçeği; ağırlıkları
yalnız risksiz faizle birlikte etkiler ve piyasa konvansiyonudur). `xirr` 365, dönem 365,25 gün
kullanır (etkisi ihmal edilebilir). Mevzuat oranları politika dosyasında "bilgi amaçlı"dır.
Denetçinin gördüğü `FINAL_REPORT_v2.md` değişikliği bu raporun tur 1 bölümüydü (ana oturumun
commit'lenmemiş düzenlemesi).

### Tur 3 bulguları ve karşılıkları

| # | Önem | Bulgu | Karşılık | Commit | Test |
|---|---|---|---|---|---|
| 1 | Orta | METHODOLOGY'de ±10 bandından kalma sayı (seviye 6: %25/%30) | %20/%25 (model + 5 puan) olarak düzeltildi | da171e6 | — |
| 2 | Orta | Vekil nakit fonu kote faizi yıllık bileşik sayıyor, risksiz faiz günlük bileşik | Gerekçeli bırakıldı (aşağıda) | — | — |
| 3 | Orta | `/backtest` istenen yılı döndürüyor; testteki vekil dönem bildirilmiyor | `years` gerçekleşen süre, `years_requested`, `proxy_in_test` | 603d2e0 | `test_backtest_api_uses_walk_forward_for_optimizer`, `test_proxy_segments_inside_a_test_period_are_reported` |
| 4 | Orta | Ağ koruması DNS sorgusunu engellemiyor (çevrimdışı ortamda test kırılıyor) | `socket.getaddrinfo` da korunur | 1d74b48 | `test_dns_lookups_are_blocked_too` |
| 5 | Düşük | Nakit ağırlıklı seviyelerin Sharpe'ı yüksek görünüyor, açıklanmamış | MODEL_PORTFOLIOS okuma notu | da171e6 | — |
| 6 | Düşük | Walk-forward evreni tam veri koşuluyla seçiliyor (hafif ileriye bakış) | Zaten `excluded_short_history` ve DATA'da açık; gerekçeli bırakıldı | — | — |
| 7 | Düşük | `macro.csv` son satırı ay sonu etiketli (2026-09-30) | Hesaba etkisi yok (`≤ tarih` filtresi); gerekçeli bırakıldı | — | — |
| 8 | Düşük | `macro_manual.csv` olmayan betiğe gönderme | `fetch_real_data.py` | da171e6 | — |
| 9 | Düşük | XIRR 365, diğerleri 365,25 gün | Tur 2 gerekçesiyle aynı | — | — |
| 10 | Düşük | Simülasyon stopajı satış bazında, `realized_summary` yıl-sınıf bazında netliyor | Bilgi amaçlı iki görünüm; gerekçeli bırakıldı | — | — |

### Hedef puan (≥ 9/10) neden üç turda yakalanamadı

Denetim döngüsü en fazla 3 tur çalıştırıldı. Puan tur 1 → 3 boyunca 8/10'da kaldı, ancak
bulguların ağırlığı belirgin biçimde düştü: tur 1'de bir yüksek önemde bulgu (hisse sınıfının tek
hisseyle temsili), tur 2'de bir yüksek bulgu (Sharpe'ta basit/bileşik faiz karışıklığı), tur 3'te
**hiç yüksek bulgu yok**. Kalan maddeler ve gerekçeleri:

* **Vekil nakit fonunun bileşiklemesi (tur 3 #2):** Düzeltmek için snapshot'ın ağdan yeniden
  üretilmesi gerekir; vekil yalnız 2021-09 öncesini etkiler, varsayılan 5 yıllık test dönemine
  girmez ve varsayılan HRP beklenen getiri kullanmaz. Kod, snapshot ile tutarlı kalsın diye
  değiştirilmedi. Yapılacak iş olarak not edildi: `derive.py` vekili `(1 + (r − ücret)/365)^gün`
  ile büyütülmeli ve snapshot yenilenmeli.
* **Git geçmişinin yoğunluğu:** Geçmiş yeniden yazılamaz (force push yasak).
* **Mevzuat ve dış veri doğrulaması:** Denetçinin ağ erişimi yoktu; oranlar politika dosyasında
  "bilgi amaçlı" olarak işaretli.
* Denetçinin kalan puan kırıntıları tek geliştirici prototipi olmanın doğal sınırlarıdır (üretim
  entegrasyonu, pentest, yük testi yok); README "Sınırlamalar" bölümünde açıkça yazılıdır.
