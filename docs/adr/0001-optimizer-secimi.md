# ADR 0001: Optimizasyon çekirdeği NumPy/SciPy ile

- Durum: Kabul edildi
- Tarih: 2026-09-24

## Bağlam
PyPortfolioOpt/riskfolio/cvxpy derleme bağımlılıkları Windows/Docker'da kırılgan ve çıktıları doğrulaması zor.

## Karar
HRP, Black-Litterman (Idzorek), min-CVaR (HiGHS LP), risk paritesi ve kısıtlı MVO tek bir kısıt modeliyle (`Constraints`) kendi implementasyonumuzdur; Ledoit-Wolf kapalı formdadır. Min-devir rebalance da L1 LP'dir (cvxpy formülasyonuyla eşdeğer).

## Sonuçlar
Deterministik, analitik vakalarla test edilir (HRP=ters varyans, BL güven uçları, CVaR kayıpsız varlık). Ağır kütüphaneler gerekmez; hmmlearn opsiyonel.
