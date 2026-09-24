"""Suitability scoring: capacity vs tolerance, consistency checks, gate.

Final risk level = ``min(capacity_level, tolerance_level)`` further capped by
knowledge/experience and hard capacity constraints (high debt, no emergency
fund, very short horizon). Contradictory answers produce warnings; blocking
ones require the investor to re-answer or explicitly confirm.

The **suitability gate** rejects allocations that contain instruments whose
risk score exceeds what the customer's level allows, or model portfolios
above the customer's level (``uygun değildir``) unless an explicit override
is acknowledged — which is then written to the audit log (yerindelik).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.policy import InvestmentPolicy, get_policy
from services.market_data.universe import BY_SYMBOL
from services.suitability.questionnaire import BY_ID, QUESTIONS


@dataclass(frozen=True)
class Warning_:
    """A consistency warning (``blocking`` ones need re-answer/confirmation)."""

    code: str
    message: str
    questions: tuple[str, ...]
    blocking: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "questions": list(self.questions),
            "blocking": self.blocking,
        }


@dataclass
class SuitabilityResult:
    """Outcome of scoring a questionnaire."""

    knowledge_score: float
    capacity_score: float
    tolerance_score: float
    capacity_level: int
    tolerance_level: int
    risk_level: int
    risk_label: str
    caps: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[Warning_] = field(default_factory=list)
    explanation: list[str] = field(default_factory=list)

    @property
    def needs_confirmation(self) -> bool:
        return any(w.blocking for w in self.warnings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "knowledge_score": round(self.knowledge_score, 1),
            "capacity_score": round(self.capacity_score, 1),
            "tolerance_score": round(self.tolerance_score, 1),
            "capacity_level": self.capacity_level,
            "tolerance_level": self.tolerance_level,
            "risk_level": self.risk_level,
            "risk_label": self.risk_label,
            "caps": self.caps,
            "warnings": [w.to_dict() for w in self.warnings],
            "needs_confirmation": self.needs_confirmation,
            "explanation": self.explanation,
        }


def _dimension_score(answers: dict[str, str], dimension: str) -> float:
    total = got = 0.0
    for q in QUESTIONS:
        if q.dimension != dimension:
            continue
        opt = q.option(answers.get(q.id, ""))
        total += q.weight * q.max_score
        got += q.weight * (opt.score if opt else 0)
    return 100.0 * got / total if total else 0.0


def score_to_level(score: float, policy: InvestmentPolicy) -> int:
    """Map a 0–100 score to a 1–10 level using the policy thresholds."""
    for idx, upper in enumerate(policy.level_upper_bounds, start=1):
        if score < upper:
            return idx
    return len(policy.level_upper_bounds)


def _raw(answers: dict[str, str], qid: str) -> int:
    opt = BY_ID[qid].option(answers.get(qid, ""))
    return opt.score if opt else 0


def consistency_warnings(answers: dict[str, str], tolerance_level: int) -> list[Warning_]:
    """Detect contradictory answer combinations."""
    out: list[Warning_] = []
    if _raw(answers, "dusus_tepkisi") == 0 and _raw(answers, "senaryo") >= 3:
        out.append(
            Warning_(
                "kayip_toleransi_celiskisi",
                "%20 düşüşte tamamını satacağınızı belirttiniz ancak yüksek oynaklıklı bir "
                "senaryo seçtiniz. Lütfen bu iki soruyu yeniden değerlendirin.",
                ("dusus_tepkisi", "senaryo"),
                blocking=True,
            )
        )
    if _raw(answers, "amac") == 0 and _raw(answers, "oz_degerlendirme") >= 3:
        out.append(
            Warning_(
                "amac_profil_celiskisi",
                "Amacınız anaparayı korumak iken kendinizi atak olarak tanımladınız.",
                ("amac", "oz_degerlendirme"),
                blocking=True,
            )
        )
    if _raw(answers, "vade") <= 1 and tolerance_level >= 7:
        out.append(
            Warning_(
                "kisa_vade_yuksek_tolerans",
                "Kısa yatırım vadesine rağmen yüksek risk toleransı beyan ettiniz; seviyeniz "
                "risk kapasitenizle sınırlandırıldı.",
                ("vade", "senaryo"),
            )
        )
    if _raw(answers, "deneyim") == 0 and _raw(answers, "bilgi") >= 4:
        out.append(
            Warning_(
                "bilgi_deneyim_celiskisi",
                "Türev ürün bilgisi beyan ettiniz ancak hiç yatırım deneyiminiz yok.",
                ("bilgi", "deneyim"),
            )
        )
    return out


def score_answers(
    answers: dict[str, str], policy: InvestmentPolicy | None = None
) -> SuitabilityResult:
    """Score a complete answer set.

    Args:
        answers: ``{question_id: option_value}`` (validated).
        policy: Investment policy (defaults to the loaded one).

    Returns:
        A :class:`SuitabilityResult` (level 1–10, warnings, explanation).
    """
    policy = policy or get_policy()
    knowledge = _dimension_score(answers, "knowledge")
    capacity = _dimension_score(answers, "capacity")
    tolerance = _dimension_score(answers, "tolerance")
    cap_level = score_to_level(capacity, policy)
    tol_level = score_to_level(tolerance, policy)
    level = min(cap_level, tol_level)
    explanation = [
        f"Risk kapasitesi puanı {capacity:.0f}/100 → seviye {cap_level}.",
        f"Risk toleransı puanı {tolerance:.0f}/100 → seviye {tol_level}.",
        f"Nihai seviye kapasite ve toleransın küçüğü: {level}.",
    ]

    caps: list[dict[str, Any]] = []
    suit = policy.raw.get("suitability", {})
    kcaps = suit.get("knowledge_caps", {})
    ccaps = suit.get("capacity_caps", {})

    def _cap(limit: int, code: str, message: str) -> None:
        nonlocal level
        if level > limit:
            caps.append({"code": code, "limit": limit, "message": message})
            explanation.append(message)
            level = limit

    if knowledge < 10:
        _cap(
            int(kcaps.get("below_10", 3)),
            "bilgi_cok_dusuk",
            "Yatırım bilgi/deneyimi çok sınırlı olduğundan seviye sınırlandı.",
        )
    elif knowledge < 25:
        _cap(
            int(kcaps.get("below_25", 5)),
            "bilgi_dusuk",
            "Yatırım bilgi/deneyimi sınırlı olduğundan seviye sınırlandı.",
        )
    if _raw(answers, "borc_orani") == 0:
        _cap(
            int(ccaps.get("high_debt", 3)),
            "yuksek_borc",
            "Borç ödemeleri gelirin yarısını aştığından seviye sınırlandı.",
        )
    if _raw(answers, "acil_durum_fonu") == 0:
        _cap(
            int(ccaps.get("no_emergency_fund", 5)),
            "acil_fon_yok",
            "Acil durum fonu bulunmadığından seviye sınırlandı; önce acil durum birikimi önerilir.",
        )
    if _raw(answers, "vade") == 0:
        _cap(
            int(ccaps.get("short_horizon", 3)),
            "kisa_vade",
            "Birikime 1 yıl içinde ihtiyaç duyulduğundan seviye sınırlandı.",
        )

    level = max(1, min(level, 10))
    return SuitabilityResult(
        knowledge_score=knowledge,
        capacity_score=capacity,
        tolerance_score=tolerance,
        capacity_level=cap_level,
        tolerance_level=tol_level,
        risk_level=level,
        risk_label=policy.risk_label(level),
        caps=caps,
        warnings=consistency_warnings(answers, tol_level),
        explanation=explanation,
    )


# ------------------------------------------------------------------- gate


@dataclass
class GateResult:
    """Suitability gate decision for an allocation."""

    allowed: bool
    violations: list[dict[str, Any]] = field(default_factory=list)
    overridden: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "violations": self.violations,
            "overridden": self.overridden,
        }


def allowed_instrument_risk(level: int, policy: InvestmentPolicy | None = None) -> int:
    policy = policy or get_policy()
    return int(policy.max_instrument_risk.get(max(1, min(int(level), 10)), 7))


def check_allocation(
    level: int,
    weights: dict[str, float],
    *,
    model_level: int | None = None,
    override_ack: bool = False,
    policy: InvestmentPolicy | None = None,
) -> GateResult:
    """Check an instrument allocation against the customer's level.

    Args:
        level: Customer risk level (1–10).
        weights: ``{symbol: weight}`` of the proposed allocation.
        model_level: Model portfolio level used for the allocation.
        override_ack: Customer explicitly accepted an unsuitable allocation.
        policy: Investment policy.

    Returns:
        :class:`GateResult`; with violations and no override → not allowed.
    """
    policy = policy or get_policy()
    max_risk = allowed_instrument_risk(level, policy)
    violations: list[dict[str, Any]] = []
    for sym, w in weights.items():
        if w <= 1e-9:
            continue
        spec = BY_SYMBOL.get(sym)
        risk = spec.risk_score if spec else 7
        if risk > max_risk:
            violations.append(
                {
                    "type": "enstruman_riski",
                    "symbol": sym,
                    "risk_score": risk,
                    "max_allowed": max_risk,
                    "message": f"{sym} (risk {risk}/7) seviyeniz için uygun değildir (en fazla {max_risk}/7).",
                }
            )
    if model_level is not None and model_level > level:
        violations.append(
            {
                "type": "model_seviyesi",
                "model_level": model_level,
                "customer_level": level,
                "message": f"Model {model_level} risk seviyeniz ({level}) için uygun değildir.",
            }
        )
    if not violations:
        return GateResult(True)
    return GateResult(allowed=override_ack, violations=violations, overridden=override_ack)


__all__ = [
    "GateResult",
    "SuitabilityResult",
    "Warning_",
    "allowed_instrument_risk",
    "check_allocation",
    "consistency_warnings",
    "score_answers",
    "score_to_level",
]
