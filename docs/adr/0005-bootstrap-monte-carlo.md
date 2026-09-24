# ADR 0005: Blok bootstrap Monte Carlo

- Durum: Kabul edildi
- Tarih: 2026-09-24

## Bağlam
GBM, TL varlıklarının kalın kuyruklarını, kur şoklarını ve enflasyon bağını yakalayamaz.

## Karar
Portföyün aylık TL getirisi ile TÜFE aynı indekslerle 6 aylık bloklarla örneklenir. Servet katkıda doğrusal olduğundan gerekli aylık katkı kantil ile tam bulunur.

## Sonuçlar
10.000 yol <1 sn; sıfır oynaklıkta analitik sonuç; what-if'ler aynı rastgele sayılarla tutarlı.
