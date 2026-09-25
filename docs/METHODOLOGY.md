# Metodoloji

## 1. Risk profilleme (yerindelik testi; kaynaklar için bkz. [COMPLIANCE](COMPLIANCE.md))
15 soru üç boyutta puanlanır: bilgi/deneyim (K), risk kapasitesi (C, objektif), risk toleransı (T,
psikolojik). Her boyut `100·Σ w_q s_q / Σ w_q max_q`; 0–100 → 1–10 eşikleri politika dosyasındadır.
**Nihai seviye = min(L_C, L_T)**, ardından sınırlar: K<10 → ≤3, K<25 → ≤5, borç/gelir >%50 → ≤3,
acil fon yok → ≤5, vade <1 yıl → ≤3. Çelişkili cevaplar engelleyici uyarı üretir; yeniden sorulur
veya açıkça onaylanır. Profil 12 ay geçerli ve versiyonludur.

## 2. Tahmin
Günlük TL getirileri (3–5 yıl). **Ledoit-Wolf**: `Σ̂ = δ·μI + (1−δ)S`, δ kapalı formda.
**James-Stein**: `μ̂ = μ̄ + (1−λ)(μ−μ̄)`, `λ = min(1, (n−2)σ̄²/T / ‖μ−μ̄‖²)`. Opsiyonel CAPM önseli
`r_f + β_i(μ_m − r_f)`. Risksiz faiz: TCMB politika faizi.

## 3. Optimizasyon (ortak kısıtlar)
`w ≥ 0`, `Σw = 1`, sınıf toplamı `∈ [m_c − b, m_c + b]` (model portföy ± bant), riskli tek enstrüman
`≤ w_max`.
* **HRP**: uzaklık `√(½(1−ρ))`, tek bağlantılı kümeleme, yarı-köşegenleştirme, yinelemeli ikiye
  bölme; sonuç kısıt kümesine Öklid izdüşümüyle taşınır.
* **Risk paritesi**: `min Σ(RC_i − 1/n)²`, `RC_i = w_i(Σw)_i / wᵀΣw`.
* **Min-CVaR** (Rockafellar–Uryasev LP): `min α + 1/((1−β)T) Σ z_t`, `z_t ≥ −r_tᵀw − α`, `z_t ≥ 0`.
* **MVO**: `max (wᵀμ − r_f)/√(wᵀΣw)`; tüm μ ≤ r_f ise minimum varyans.
* **Black-Litterman**: `π = r_f + δΣw_mkt`,
  `μ_BL = [(τΣ)⁻¹ + PᵀΩ⁻¹P]⁻¹[(τΣ)⁻¹π + PᵀΩ⁻¹Q]`. **Ω, Idzorek (2005) yöntemiyle sayısal çözülür**:
  her görüş *k* için tek başına %100 güvenle oluşan ima edilen ağırlık sapması
  `Δw_100 = (δΣ)⁻¹(μ_100 − r_f) − w_mkt` hesaplanır; ω_k, görüşün tek başına yarattığı sapmanın
  `Δw_100` üzerindeki izdüşümü `c_k · Δw_100` olacak şekilde `brentq` ile (1 boyutlu kök arama)
  bulunur. Oran ω_k'da kesin azalan olduğundan kök tektir. Kısıtsız ima edilen ağırlıklarda bu kök,
  PyPortfolioOpt'un `omega="idzorek"` seçeneğinde kullandığı kapalı form
  `ω_k = τ(1−c_k)/c_k · p_kΣp_kᵀ` ile çakışır (Walters 2014'te türetilir); testler hem hedef
  sapma oranını hem PyPortfolioOpt ile eşitliği doğrular (`tests/test_v2_optimizer.py`).
* Rejim eğilimi (±%5) savunmacı ↔ BIST sınıfları arasında ağırlık kaydırır.
* Sonuçta hangi sınıf sınırının / enstrüman tavanının **bağlayıcı** olduğu
  `params.binding_constraints` alanında raporlanır.

### Yöntemler neden çoğu zaman benzer sonuç veriyor, ne zaman ayrışıyor?
* Sınıf toplamları model portföyün ±5 puan bandına bağlıdır (uygunluk çapası; v2 denetiminde ±10'dan daraltıldı — ±10 ile HRP ve stres rejimi eğilimi çok agresif bir profile savunmacı bir dağılım önerebiliyordu). HRP, min-CVaR,
  min-varyans ve (TL faizinin yüksek olduğu dönemde) maks-Sharpe, riski en düşük sınıfı — TL para
  piyasasını — sevdiği için bu sınıf çoğunlukla **üst sınırına yapışır**. Seviye 6'da para piyasası
  %25'e, TL tahvil %30'a oturur: portföyün yarısından fazlası yöntemden değil politikadan gelir.
  Bu durum `binding_constraints` ile açıkça gösterilir.
* Yöntemlerin gerçek söz hakkı **kalan riskli dilimde ve sınıf içindedir**: hisse sepeti, döviz
  (USD/EUR), endeks (XU100/XU030) ve fon alternatifleri arasında. Sentetik, kalın kuyruklu
  (Student-t, ν=3) yüksek volatilite rejiminde seviye 6 için HRP ile min-CVaR arasındaki toplam
  ağırlık farkı L1 = 0,38'dir: HRP hisse sepetine ~%14 dağıtırken min-CVaR kuyruk kaybı nedeniyle
  hisseyi sıfırlayıp eurobond'u %15'e çıkarır (`test_hrp_and_min_cvar_differ_in_high_vol_regime`).
* Düşük volatiliteli, korelasyonların istikrarlı olduğu dönemlerde kovaryans tahmini yöntemleri
  birbirine yaklaştırır; ayrışma kuyruk riski (CVaR), korelasyon kümeleri (HRP) ve getiri
  görüşleri (Black-Litterman) belirginleştiğinde artar.

## 4. Yeniden dengeleme
Drift bantları göreli + mutlaktır (Betterment tarzı): enstrüman bandı
`b_i = min(b_tavan, max(b_taban, ρ·w*_i))` (varsayılan %0,5 / %25 / %5), sınıf toplamı için mutlak
`b_sınıf = %3`; değerler `config/policy.toml` → `[rebalance.drift_band]`. Böylece hedefi %1,3 olan
bir hisse %0,5 puanlık bant alır; %7,6'ya çıkması (hedefin 5,8 katı) rebalance'ı tetikler
(`tests/test_v2_bands.py::test_asels_scenario_triggers_rebalance`). Tetik: herhangi bir enstrüman
veya sınıf bant dışında ya da atıl nakit > `c_max`. LP (HiGHS):
`min Σ(1+k_i)b_i + (1+κ+k_i)s_i + εΣd_i` s.t. enstrüman ve sınıf bantları (%90 güvenlik payı),
`0 ≤ C − Σ(1+k)b + Σ(1−k)s ≤ ½c_max·V`, `d_i ≥ |v_i + b_i − s_i − w*_i V|`, `s_i ≤ v_i`.
κ satış cezası (vergi), ε hedefe çekim → **yeni nakit önce en düşük ağırlıklılara** gider.
Maliyet = komisyon + BSMV + yarım spread (+asgari ücret). Stopaj lot bazında (FIFO/HIFO), elde
tutma süresine göre tablo. Broker: `p_dolum = p·(1 ± slipaj)`.

## 5. Hedef Monte Carlo
Aylık nominal TL portföy getirisi ve aylık TÜFE **aynı indekslerle** blok bootstrap edilir (L=6 ay,
10.000 yol, sabit tohum). `W_{t+1} = (W_t + c)·(1+r_t)/(1+π_t)`. Sabit yollar için `W_T = A + cB`
(`A = W_0Πg`, `B = Σ_t Π_{s≥t} g_s`) → başarı olasılığı `P(A + cB ≥ K)` ve gerekli katkı
`c* = Q_p(max(0,(K−A_i)/B_i))` **tam** hesaplanır. Sıfır oynaklıkta analitik gelecek değere eşittir.

## 6. Analitik (tek motor: `services/analytics/engine.py`)
* **Ledger yeniden kurulumu:** bugünkü pozisyon ve nakitten geriye doğru işlemler ve nakit akışları
  sarılır. Her olay bir *değerleme gününe* eşlenir: hafta sonu/tatil olayları bir sonraki işlem
  gününün kapanışına, son fiyat gününden sonraki olaylar son güne taşınır (roll-forward); pencere
  öncesi olaylar açılış durumunun içindedir.
* **Akış sınıflandırması** (GIPS 2020 tanımı, https://www.gipsstandards.org/wp-content/uploads/2021/03/2020_gips_standards_firms.pdf): yalnızca müşterinin yatırma/çekme işlemleri dış
  akıştır. Temettü, kupon, faiz, ücret, vergi ve komisyon portföy içidir ve TWR'a girer.
* **TWR:** `r_t = (V_t − F_t)/V_{t−1} − 1` (F yalnız dış akış, gün sonu varsayımı), fonlanmamış
  günler hariç. `twr_cumulative` dönemin zincirlenmiş getirisi; `twr_annualized` takvim günüyle
  `(1+TWR)^(365,25/gün) − 1` ve **bir yıldan kısa dönemde verilmez**. **MWR:** XIRR (Brent), yıllık,
  aynı kuralla. Her yanıt `period` (başlangıç, bitiş, gün, yıl) taşır.
* **Tear sheet:** CAGR (aynı takvim kuralı), vol, **Sharpe = (ortalama dönemsel getiri − r_f,dönem)·N /
  σ_yıllık** (ex-post). N (yıllık gözlem sayısı) tarihlerden çıkarılır: snapshot'ta yılda ~261 hafta içi
  satır vardır, sabit 252 kullanmak dönemi uzatıp CAGR'ı düşürüyordu. Risksiz faiz, ölçülen dönemde
  geçerli TCMB politika faizlerinin ortalamasıdır (bugünkü faiz değil). Sortino, Calmar, MDD,
  tarihsel/parametrik VaR-CVaR %95, XU100 beta/TE/IR. `/performance` artık `/report`'un
  kullanımdan kalkan bir özetidir; ikisi aynı sayıları döndürür.
* **Backtest:** rebalance yok / takvim / bant (canlı motorla aynı bant kuralı), maliyetli.

## 7. Stres ve rejim
Faktör betaları: aylık OLS `r_i = a + β·[BIST, USDTRY, altın(USD), Δfaiz]`, `ΔV = Σ V_i β_i·şok`.
Tarihsel senaryolar pencere tekrarıdır. Rejim: GaussianHMM(3) — durumlar ortalama BIST getirisine göre
boğa/yatay/stres; hmmlearn yoksa 3 aylık getiri/volatilite eşikleri.
