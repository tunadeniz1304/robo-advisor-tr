"""SPK III-37.1 style suitability questionnaire (Turkish, 15 questions).

Three dimensions are scored independently:

    * ``knowledge``  — bilgi ve deneyim (uygunluk: karmaşık ürünlere erişim),
    * ``capacity``   — objektif risk taşıma kapasitesi (yaş, gelir, istikrar,
      birikim, borç, acil durum fonu, vade, varlık payı),
    * ``tolerance``  — psikolojik risk toleransı (kayıp senaryoları, hedef,
      öz değerlendirme).

Every option carries a score; the maximum per question normalises it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

QUESTIONNAIRE_VERSION = "SPK-2026.1"


@dataclass(frozen=True)
class Option:
    value: str
    label: str
    score: int


@dataclass(frozen=True)
class Question:
    id: str
    section: str
    dimension: str  # knowledge | capacity | tolerance
    text: str
    options: tuple[Option, ...]
    weight: float = 1.0
    help: str | None = None

    @property
    def max_score(self) -> int:
        return max(o.score for o in self.options)

    def option(self, value: str) -> Option | None:
        return next((o for o in self.options if o.value == value), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "section": self.section,
            "dimension": self.dimension,
            "text": self.text,
            "help": self.help,
            "options": [{"value": o.value, "label": o.label} for o in self.options],
        }


def _opts(*items: tuple[str, str, int]) -> tuple[Option, ...]:
    return tuple(Option(v, label, s) for v, label, s in items)


QUESTIONS: tuple[Question, ...] = (
    # --- Bilgi ve deneyim ------------------------------------------------------
    Question(
        "bilgi",
        "Bilgi",
        "knowledge",
        "Aşağıdakilerden hangisi yatırım araçları hakkındaki bilginizi en iyi tanımlar?",
        _opts(
            ("yok", "Mevduat dışında bilgim yok", 0),
            ("temel", "Mevduat, döviz ve altını bilirim", 1),
            ("fon", "Yatırım fonları ve devlet tahvillerini bilirim", 2),
            ("hisse", "Hisse senedi, eurobond ve BYF'leri bilirim", 3),
            ("turev", "Vadeli işlemler ve opsiyon gibi türev ürünleri bilirim", 4),
        ),
    ),
    Question(
        "deneyim",
        "Deneyim",
        "knowledge",
        "Son 3 yılda hangi araçlarla fiilen yatırım yaptınız?",
        _opts(
            ("yok", "Hiç yatırım yapmadım", 0),
            ("mevduat", "Yalnızca mevduat / döviz / altın", 1),
            ("fon", "Yatırım fonu veya tahvil", 2),
            ("hisse", "Hisse senedi veya eurobond", 3),
            ("turev", "Türev ürünler / kaldıraçlı işlemler", 4),
        ),
    ),
    Question(
        "islem_sikligi",
        "Deneyim",
        "knowledge",
        "Yılda ortalama kaç kez yatırım işlemi yaparsınız?",
        _opts(
            ("hic", "Hiç", 0),
            ("az", "1–5 kez", 1),
            ("orta", "6–20 kez", 2),
            ("cok", "20'den fazla", 3),
        ),
    ),
    # --- Mali durum (kapasite) ----------------------------------------------------
    Question(
        "yas",
        "Mali durum",
        "capacity",
        "Yaşınız hangi aralıkta?",
        _opts(
            ("u25", "25 yaş altı", 4),
            ("25_35", "25–35", 4),
            ("36_45", "36–45", 3),
            ("46_55", "46–55", 2),
            ("56_65", "56–65", 1),
            ("65p", "65 üstü", 0),
        ),
    ),
    Question(
        "gelir",
        "Mali durum",
        "capacity",
        "Hanenizin aylık net geliri hangi aralıkta?",
        _opts(
            ("g0", "30.000 TL altı", 0),
            ("g1", "30.000–60.000 TL", 1),
            ("g2", "60.000–120.000 TL", 2),
            ("g3", "120.000–250.000 TL", 3),
            ("g4", "250.000 TL üstü", 4),
        ),
        help="Gelir bilgisi yalnızca bant olarak saklanır ve üçüncü taraflarla paylaşılmaz.",
    ),
    Question(
        "gelir_istikrari",
        "Mali durum",
        "capacity",
        "Gelirinizin istikrarını nasıl değerlendirirsiniz?",
        _opts(
            ("duzensiz", "Düzensiz / belirsiz", 0),
            ("dalgali", "Dalgalı", 1),
            ("duzenli", "Düzenli", 2),
            ("guvenceli", "Çok güvenceli (kamu, emekli maaşı vb.)", 3),
        ),
    ),
    Question(
        "birikim_orani",
        "Mali durum",
        "capacity",
        "Giderleriniz düşüldükten sonra gelirinizin ne kadarını biriktirebiliyorsunuz?",
        _opts(
            ("b0", "%5'ten az", 0),
            ("b1", "%5–15", 1),
            ("b2", "%15–30", 2),
            ("b3", "%30'dan fazla", 3),
        ),
    ),
    Question(
        "borc_orani",
        "Mali durum",
        "capacity",
        "Aylık kredi/borç ödemeleriniz gelirinizin ne kadarı?",
        _opts(
            ("d0", "%50'den fazla", 0),
            ("d1", "%30–50", 1),
            ("d2", "%10–30", 2),
            ("d3", "%10'dan az / yok", 3),
        ),
    ),
    Question(
        "acil_durum_fonu",
        "Mali durum",
        "capacity",
        "Kaç aylık giderinizi karşılayacak acil durum birikiminiz var?",
        _opts(
            ("yok", "Yok", 0),
            ("a1", "3 aydan az", 1),
            ("a2", "3–6 ay", 2),
            ("a3", "6 aydan fazla", 3),
        ),
    ),
    Question(
        "vade",
        "Hedef",
        "capacity",
        "Bu birikime ne zaman ihtiyaç duymayı planlıyorsunuz?",
        _opts(
            ("v0", "1 yıl içinde", 0),
            ("v1", "1–3 yıl", 1),
            ("v2", "3–5 yıl", 2),
            ("v3", "5–10 yıl", 3),
            ("v4", "10 yıldan uzun", 4),
        ),
    ),
    Question(
        "varlik_payi",
        "Mali durum",
        "capacity",
        "Bu yatırım, toplam birikiminizin ne kadarını oluşturuyor?",
        _opts(
            ("p0", "%75'ten fazla", 0),
            ("p1", "%50–75", 1),
            ("p2", "%25–50", 2),
            ("p3", "%25'ten az", 3),
        ),
    ),
    # --- Hedef ve tolerans ------------------------------------------------------------
    Question(
        "amac",
        "Hedef",
        "tolerance",
        "Yatırımınızın ana amacı nedir?",
        _opts(
            ("koruma", "Anaparayı enflasyona karşı korumak", 0),
            ("gelir", "Düzenli gelir elde etmek", 1),
            ("dengeli", "Dengeli büyüme", 2),
            ("buyume", "Uzun vadede yüksek büyüme", 3),
        ),
    ),
    Question(
        "dusus_tepkisi",
        "Tolerans",
        "tolerance",
        "Portföyünüz 3 ay içinde %20 değer kaybederse ne yaparsınız?",
        _opts(
            ("hepsini_sat", "Tamamını satarım", 0),
            ("kismen_sat", "Bir kısmını satarım", 1),
            ("bekle", "Beklerim", 2),
            ("alim", "Ek alım yaparım", 3),
        ),
        weight=1.5,
    ),
    Question(
        "senaryo",
        "Tolerans",
        "tolerance",
        "1 yıllık en kötü / en iyi senaryosu aşağıdaki gibi olan hangi portföyü seçerdiniz?",
        _opts(
            ("a", "A: −%2 / +%5", 0),
            ("b", "B: −%8 / +%12", 1),
            ("c", "C: −%15 / +%22", 2),
            ("d", "D: −%25 / +%35", 3),
            ("e", "E: −%35 / +%50", 4),
        ),
        weight=1.5,
    ),
    Question(
        "oz_degerlendirme",
        "Tolerans",
        "tolerance",
        "Yatırımcı olarak kendinizi nasıl tanımlarsınız?",
        _opts(
            ("cok_temkinli", "Çok temkinli", 0),
            ("temkinli", "Temkinli", 1),
            ("dengeli", "Dengeli", 2),
            ("atak", "Atak", 3),
            ("cok_atak", "Çok atak", 4),
        ),
    ),
)

BY_ID: dict[str, Question] = {q.id: q for q in QUESTIONS}


@dataclass
class AnswerSet:
    """Validated answers: ``{question_id: option_value}``."""

    answers: dict[str, str]
    missing: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing and not self.invalid


def validate_answers(raw: dict[str, str]) -> AnswerSet:
    """Check that every question has a valid option."""
    missing = [q.id for q in QUESTIONS if q.id not in raw]
    invalid = [qid for qid, v in raw.items() if qid in BY_ID and BY_ID[qid].option(str(v)) is None]
    unknown = [qid for qid in raw if qid not in BY_ID]
    return AnswerSet(
        answers={k: str(v) for k, v in raw.items() if k in BY_ID},
        missing=missing,
        invalid=invalid + unknown,
    )


def questionnaire_payload() -> dict[str, Any]:
    """Public questionnaire definition (no scores exposed)."""
    return {
        "version": QUESTIONNAIRE_VERSION,
        "sections": ["Bilgi", "Deneyim", "Mali durum", "Hedef", "Tolerans"],
        "questions": [q.to_dict() for q in QUESTIONS],
    }


__all__ = [
    "BY_ID",
    "QUESTIONNAIRE_VERSION",
    "QUESTIONS",
    "AnswerSet",
    "Option",
    "Question",
    "questionnaire_payload",
    "validate_answers",
]
