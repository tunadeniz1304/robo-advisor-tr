# Uyum notları (bilgi amaçlı)

## SPK III-37.1 — uygunluk ve yerindelik
* Bilgi/deneyim, mali durum, yatırım amacı ve risk toleransı ayrı ayrı sorgulanır; sonuç versiyonlu
  saklanır (`risk_profiles`), 12 ay sonra yenileme istenir.
* **Uygunluk kapısı:** müşteri seviyesinin izin verdiğinden riskli enstrüman veya daha yüksek seviyeli
  model portföy önerilemez. Müşteri açıkça isterse "uygun değildir" uyarısı gösterilir, açık onay
  (`override_ack`) alınır ve `suitability.override` olarak hash-zincirli denetim kaydına yazılır.
* Emirler yalnızca insan onayıyla yürütülür; LLM ve Copilot'un yürütme yetkisi yoktur.

## KVKK (7499 sayılı Kanun değişiklikleri) ve yurt dışına aktarım
* LLM sağlayıcısına giden her bağlam `llm/redaction.py`'den geçer: ad, e-posta, telefon, TCKN
  düşürülür; müşteri kimliği `MUSTERI_12` takma adına, gelir bant bilgisine çevrilir. Böylece yurt
  dışındaki bir modele kişisel veri aktarılmaz; yalnızca anonim finansal sayılar gider.
* E-posta ve gelir veritabanında Fernet ile şifrelidir; e-posta tekilliği HMAC kör indeksle sağlanır.
* `llm_usage` yalnızca meta veri (amaç, token, gecikme, mod) tutar; içerik saklanmaz.

## Denetim izi
`audit_log` SHA-256 hash zinciridir; `GET /api/v1/audit/verify` ilk bozulan kaydı gösterir. Girişler,
müşteri değişiklikleri, işlemler, profiller, öneri yaşam döngüsü, BL görüşleri, Autopilot ve Copilot
araç çağrıları kaydedilir.

## Vergi ve maliyet
Stopaj tablosu ve maliyet oranları `config/policy.toml` içindedir ve bilgi amaçlıdır; güncel mevzuat
kontrol edilmelidir.
