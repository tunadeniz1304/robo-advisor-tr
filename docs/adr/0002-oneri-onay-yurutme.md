# ADR 0002: Öneri → insan onayı → yürütme

- Durum: Kabul edildi
- Tarih: 2026-09-24

## Bağlam
Robo-danışmanın müşteri adına işlem yapması yerindelik ve güven gerektirir; v1 LLM hatasında çift rebalance yapıyordu.

## Karar
Her rebalance önce `rebalance_proposals` durum makinesinde ONAY_BEKLIYOR üretir. LangGraph `interrupt` ile graf duraklar, kalıcı SQLite checkpointer ile yeniden başlatmaya dayanır. Onay koşullu UPDATE (tek geçiş), idempotency anahtarı, portföy kilidi, TTL ve fiyat-değişimi kontrolüyle korunur.

## Sonuçlar
Çift onay emirleri iki kez yürütmez (eşzamanlı test). Zamanlayıcı/Copilot/Autopilot da aynı öneri yolunu kullanır.
