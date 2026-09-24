"""Modular prompt templates for the multi-agent workflow.

Every template is a plain, readable string in Turkish with typed ``{fields}``;
no prompt is constructed by string concatenation elsewhere. The separation lets
the LLM prompts evolve (or be A/B-tested) without touching agent code.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Market Agent — veri toplama ajanı; LLM kullanmaz (saf yfinance hesaplaması).
# Üretimde ajanlar LLM'i yalnızca Portföy Yöneticisi'nde çağırır.
# ---------------------------------------------------------------------------

# Risk Agent da saf hesaplama yapar (RiskService), prompt içermez.

# ---------------------------------------------------------------------------
# Portföy Yöneticisi — Markowitz çıktısını LLM analiziyle birleştirir.
# ---------------------------------------------------------------------------

PORTFOLIO_MANAGER_SYSTEM = (
    "Sen kurumsal bir portföy yöneticisisin. Görevin, Modern Portföy Teorisi "
    "(Markowitz) tarafından hesaplanan hedef ağırlıkları müşterinin dinamik "
    "risk profili ve güncel piyasa koşulları ışığında değerlendirmektir. "
    "Rasyonel, veri odaklı ve kısa analiz yap. Türkçe yanıt ver."
)

PORTFOLIO_MANAGER_USER = """
MÜŞTERİ RİSK PROFİLİ:
{risk_profile}

HEDEF AĞIRLIKLAR (MARKOWITZ MPT):
{markowitz_weights}

GÜNCEL PİYASA GÖRÜNÜMÜ:
{market_snapshot}

MEVCUT PORTFÖY:
{current_holdings}

Lütfen şunları değerlendir:
1. Hedef ağırlıklar, müşterinin risk profiliyle uyumlu mu?
2. Piyasa momentumu ve oynaklık dikkate alındığında endişe edilecek
   herhangi bir varlık var mı?
3. Önerilen dağılım için kısa (3-5 cümle) bir gerekçe yaz.
"""

# ---------------------------------------------------------------------------
# Özet rapor (isteğe bağlı) — tüm grafik aktıktan sonra son kullanıcıya.
# ---------------------------------------------------------------------------

FINAL_REPORT_SYSTEM = (
    "Sen bankacılık kanalı yatırım danışmanısın. Çıktı, müşterinin anlayacağı "
    "dilde, teknik jargondan arındırılmış, profesyonel bir Türkçe özet olmalı."
)

FINAL_REPORT_USER = """
Yatırımcı profili: {risk_category} (skor {risk_score}).
Hedef dağılım: {weights}
Gerçekleştirilen işlemler: {orders}

Bu bilgileri müşteriye sunulacak kısa, net ve güven veren bir özet metne
dönüştür. "Yatırım danışmanlığı değildir" ibaresini mutlaka ekle.
"""

# Modüler prompt rejistresi: adlandırılmış erişim (genişletilebilir).
PROMPT_REGISTRY = {
    "portfolio_manager_system": PORTFOLIO_MANAGER_SYSTEM,
    "portfolio_manager_user": PORTFOLIO_MANAGER_USER,
    "final_report_system": FINAL_REPORT_SYSTEM,
    "final_report_user": FINAL_REPORT_USER,
}
