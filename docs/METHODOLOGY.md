# Metodoloji

## 1. Risk profilleme (SPK III-37.1)
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
* **Black-Litterman**: `π = r_f + δΣw_mkt`, `Ω_kk = (1−c_k)/c_k · τ p_kΣp_kᵀ` (Idzorek),
  `μ_BL = [(τΣ)⁻¹ + PᵀΩ⁻¹P]⁻¹[(τΣ)⁻¹π + PᵀΩ⁻¹Q]`.
* Rejim eğilimi (±%5) savunmacı ↔ BIST sınıfları arasında ağırlık kaydırır.

## 4. Yeniden dengeleme
Drift: `|w_i − w*_i| > b_sınıf` veya atıl nakit > `c_max`. LP (HiGHS):
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

## 6. Analitik
TWR: `r_t = (V_t − F_t)/V_{t−1} − 1` (fonlanmamış günler hariç). MWR: XIRR (Brent). Tear sheet: CAGR,
vol, Sharpe, Sortino, Calmar, MDD, tarihsel/parametrik VaR-CVaR %95, XU100 beta/TE/IR. Backtest:
rebalance yok / takvim / bant, maliyetli.

## 7. Stres ve rejim
Faktör betaları: aylık OLS `r_i = a + β·[BIST, USDTRY, altın(USD), Δfaiz]`, `ΔV = Σ V_i β_i·şok`.
Tarihsel senaryolar pencere tekrarıdır. Rejim: GaussianHMM(3) — durumlar ortalama BIST getirisine göre
boğa/yatay/stres; hmmlearn yoksa 3 aylık getiri/volatilite eşikleri.
