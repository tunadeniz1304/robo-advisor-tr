# ADR 0003: LLM sayı üretmez, emir vermez

- Durum: Kabul edildi
- Tarih: 2026-09-24

## Bağlam
Finansal metinde uydurulmuş bir yüzde veya tutar kabul edilemez; LLM erişilemez olabilir.

## Karar
Tüm ağırlık/emir/olasılıklar deterministik servislerden gelir. LLM yalnızca JSON şemalı metin üretir; metindeki her yüzde/tutar bağlam JSON'undaki bir sayıyla eşleşmezse reddedilir, 1 onarım denenir, sonra deterministik demo çıktısına düşülür. KVKK redaction ve `<data>` etiketleriyle injection koruması uygulanır.

## Sonuçlar
Anahtarsız tam çalışma; canlı mod hatalarında akış bozulmaz (`llm_mode=fallback`).
