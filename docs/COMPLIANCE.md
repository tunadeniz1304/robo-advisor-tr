# Uyum notları (bilgi amaçlı)

Bu belge, prototipin hangi düzenleme fikirlerinden ilham aldığını ve bunları nasıl
yaklaşıkladığını anlatır. **Hukuki görüş değildir**; her atıf resmî kaynağa bağlantılıdır.
Bağlantısı verilemeyen iddia bu belgede tutulmaz. Son kontrol: 2026-09-25.

## Yatırımcı profili: yerindelik testi
* SPK'nın açıklamasına göre bireysel portföy yöneticiliği ve yatırım danışmanlığı hizmetinden önce
  **yerindelik testi** yapılır (Yatırım Hizmetleri Tebliği III-37.1, md. 40); genel müşterilere
  yapılan **uygunluk testi** ise III-39.1 sayılı Tebliğ md. 33'tedir. Kaynak:
  https://spk.gov.tr/kurumlar/yatirim-kuruluslari/araci-kurumlar/uygunluk-ve-yerindelik-testleri
* Uygulamadaki karşılığı: bilgi/deneyim, mali durum (kapasite), yatırım amacı/vade ve risk
  toleransı ayrı ayrı puanlanır; sonuç versiyonlu saklanır (`risk_profiles`) ve 12 ay sonra
  yenileme istenir. Kod içinde bu modül "suitability" adını taşır; Türkçe metinlerde
  "yerindelik" kullanılmalıdır.
* **Uygunsuzluk kapısı:** müşteri seviyesinin izin verdiğinden riskli enstrüman veya daha yüksek
  seviyeli model portföy önerilemez. Müşteri açıkça isterse uyarı gösterilir, açık onay
  (`override_ack`) alınır ve `suitability.override` olarak hash zincirli denetim kaydına yazılır.
* Emirler yalnızca insan onayıyla yürütülür; LLM ve Copilot'un yürütme yetkisi yoktur.
* Mevzuatın tam metni ve rehberleri SPK mevzuat sisteminde: https://mevzuat.spk.gov.tr/

## Kişisel veriler ve yurt dışına aktarım
* 6698 sayılı KVKK'nın yurt dışına aktarım maddesi (md. 9), 7499 sayılı Kanunla değiştirildi
  (12.3.2024 tarihli Resmî Gazete, 1.6.2024'te yürürlük); açık rıza merkezli yapıdan
  "yeterlilik kararı → uygun güvenceler → arızi haller" sırasına geçildi. Kaynak: KVKK
  "Kişisel Verilerin Yurt Dışına Aktarılması Rehberi",
  https://www.kvkk.gov.tr/Icerik/8142/Kisisel-Verilerin-Yurt-Disina-Aktarilmasi-Rehberi
* Uygulamadaki karşılığı (teknik önlem; hukuki değerlendirme yerine geçmez): LLM sağlayıcısına
  giden her bağlam `llm/redaction.py`'den geçer; ad, e-posta, telefon ve TC kimlik numarası
  düşürülür, müşteri kimliği `MUSTERI_12` takma adına, gelir bant bilgisine çevrilir. Yurt
  dışındaki bir modele yalnızca anonim finansal sayılar gider.
* E-posta ve gelir veritabanında Fernet ile şifrelidir; e-posta tekilliği HMAC kör indeksle
  sağlanır. Prod ortamında şifreleme anahtarı olmadan uygulama başlamaz.
* `llm_usage` yalnızca meta veri (amaç, token, gecikme, mod) tutar; içerik saklanmaz.

## Denetim izi
`audit_log` SHA-256 hash zinciridir; `GET /api/v1/audit/verify` ilk bozulan kaydı gösterir. Girişler,
müşteri değişiklikleri, işlemler, nakit akışları, profiller, öneri yaşam döngüsü, BL görüşleri,
Autopilot ve Copilot araç çağrıları kaydedilir. Birden çok uygulama örneğinde zincire ekleme
paylaşılan (Redis) kilitle sıralanır.

## Performans raporlama
Nakit akışı sınıflandırması ve yıllıklandırma kuralı GIPS standardındaki tanımları izler: dış
nakit akışı yalnızca portföye giren/çıkan sermayedir; bir yıldan kısa dönem getirisi
yıllıklandırılmaz. Kaynak: GIPS 2020 Standards for Firms,
https://www.gipsstandards.org/wp-content/uploads/2021/03/2020_gips_standards_firms.pdf —
proje bir GIPS uyum beyanı **iddia etmez** (bileşik, doğrulama vb. yoktur).

## Vergi ve maliyet
Stopaj tablosu ve maliyet oranları `config/policy.toml` içindedir; **örnek / bilgi amaçlıdır ve
yapılandırmadan okunur**. Güncel oranlar için resmî kaynak (GİB) kontrol edilmelidir; bu belge
belirli bir oranın yürürlükte olduğunu iddia etmez. Vergi zararı hasadı simülasyonu yalnızca aynı
varlık sınıfında bu yıl gerçekleşmiş kazançla sınırlı bir tahmin verir.
