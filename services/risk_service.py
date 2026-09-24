"""Dynamic risk assessment service.

The Risk Agent reads the customer profile from the database and computes a
*dynamic* risk score in [0, 100] using a transparent, documented formula —
not a hard-coded label. The score feeds the Portfolio Manager in two ways:

    * the target risk bucket (Conservative / Balanced / Growth / Aggressive),
    * a maximum equity weight that anchors the Markowitz optimisation.

The scoring model combines:

    * declared tolerance  (1..5)        — the client's stated appetite,
    * investment horizon  (1..50 years) — longer horizon tolerates more risk,
    * income level         (TL/month)   — higher income raises loss-absorption
      capacity (log-scaled, with a floor).

The formula is deterministic and unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.logging import get_logger
from models import Customer

logger = get_logger("otonom.risk")


@dataclass(frozen=True)
class RiskAssessment:
    """Result of the dynamic risk analysis for a customer."""

    score: float  # 0..100
    category: str  # Conservative | Balanced | Growth | Aggressive
    max_equity_weight: float  # 0..1 maximum allowed equity allocation
    rationale: str

    def to_dict(self) -> dict[str, object]:
        """JSON-friendly representation for the state graph."""
        return {
            "score": round(self.score, 2),
            "category": self.category,
            "max_equity_weight": self.max_equity_weight,
            "rationale": self.rationale,
        }


class RiskService:
    """Computes a customer's dynamic risk score from their DB profile."""

    # ---- Scoring constants (documented, tunable) ---------------------------
    TOLERANCE_WEIGHT = 0.5
    HORIZON_WEIGHT = 0.3
    INCOME_WEIGHT = 0.2

    # Horizon (years) -> contribution in [0,1] on the horizon axis.
    MAX_HORIZON_YEARS = 30.0

    # Income (TL/month) -> contribution on the income axis (log scale).
    # Logarithmic because 10k -> 100k TL changes capacity far more meaningfully
    # than 1M -> 2M TL; clamp to [0,1].
    INCOME_LOG_FLOOR = 10_000.0
    INCOME_LOG_CEIL = 300_000.0

    @staticmethod
    def _horizon_factor(years: float) -> float:
        """Longer horizon -> higher risk capacity, saturating at MAX years."""
        return min(years / RiskService.MAX_HORIZON_YEARS, 1.0)

    @staticmethod
    def _income_factor(income: float) -> float:
        """Log-scaled income capacity factor in [0, 1]."""
        import math

        if income <= RiskService.INCOME_LOG_FLOOR:
            return 0.1
        if income >= RiskService.INCOME_LOG_CEIL:
            return 1.0
        low = math.log(RiskService.INCOME_LOG_FLOOR)
        high = math.log(RiskService.INCOME_LOG_CEIL)
        value = math.log(max(income, RiskService.INCOME_LOG_FLOOR))
        return (value - low) / (high - low)

    @classmethod
    def category_bounds(cls, score: float) -> tuple[str, float]:
        """Map a 0..100 score to (category, max equity weight).

        Buckets:
            Conservative  (< 40)  -> 30% equity ceiling
            Balanced      (< 60)  -> 50% equity ceiling
            Growth        (< 80)  -> 70% equity ceiling
            Aggressive    (>= 80) -> 90% equity ceiling
        """
        if score < 40:
            return "Conservative", 0.30
        if score < 60:
            return "Balanced", 0.50
        if score < 80:
            return "Growth", 0.70
        return "Aggressive", 0.90

    def assess(self, customer: Customer) -> RiskAssessment:
        """Compute the dynamic risk score for a customer.

        Args:
            customer: ORM customer with profile inputs loaded from the DB.

        Returns:
            A :class:`RiskAssessment` with score, category, equity ceiling and
            a human-readable rationale (Turkish).
        """
        tolerance = max(1, min(customer.declared_risk_tolerance, 5))
        tolerance_norm = (tolerance - 1) / 4.0  # 0..1

        horizon_norm = self._horizon_factor(customer.investment_horizon_years)
        income_norm = self._income_factor(customer.monthly_income)

        score = 100.0 * (
            self.TOLERANCE_WEIGHT * tolerance_norm
            + self.HORIZON_WEIGHT * horizon_norm
            + self.INCOME_WEIGHT * income_norm
        )
        score = max(0.0, min(score, 100.0))

        category, max_equity = self.category_bounds(score)
        rationale = (
            f"Beyan edilen tolerans {tolerance}/5, yatırım ufku "
            f"{customer.investment_horizon_years} yıl, aylık gelir "
            f"~{customer.monthly_income:,.0f} TL -> dinamik skor "
            f"{score:.1f}/100 ({category})."
        )
        logger.info(
            "risk_assessed",
            customer_id=customer.id,
            score=round(score, 2),
            category=category,
            max_equity_weight=max_equity,
        )
        return RiskAssessment(
            score=score, category=category, max_equity_weight=max_equity, rationale=rationale
        )


__all__ = ["RiskService", "RiskAssessment"]
