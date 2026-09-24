"""Suitability persistence: versioned, time-limited risk profiles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.policy import get_policy
from models import Customer, RiskProfile
from models.base import utcnow
from services.risk_service import RiskService
from services.suitability.questionnaire import QUESTIONNAIRE_VERSION
from services.suitability.scoring import SuitabilityResult


@dataclass(frozen=True)
class EffectiveLevel:
    """Risk level used for advice, with its provenance."""

    level: int
    label: str
    source: str  # profil | tahmini
    profile_id: int | None
    valid_until: datetime | None
    expired: bool
    renewal_due: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "label": self.label,
            "source": self.source,
            "profile_id": self.profile_id,
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
            "expired": self.expired,
            "renewal_due": self.renewal_due,
        }


def profile_to_dict(profile: RiskProfile) -> dict[str, Any]:
    now = utcnow()
    return {
        "id": profile.id,
        "customer_id": profile.customer_id,
        "version": profile.version,
        "questionnaire_version": profile.questionnaire_version,
        "risk_level": profile.risk_level,
        "risk_label": get_policy().risk_label(profile.risk_level),
        "capacity_level": profile.capacity_level,
        "tolerance_level": profile.tolerance_level,
        "capacity_score": profile.capacity_score,
        "tolerance_score": profile.tolerance_score,
        "knowledge_score": profile.knowledge_score,
        "warnings": profile.warnings,
        "explanation": profile.explanation,
        "is_active": profile.is_active,
        "valid_until": profile.valid_until.isoformat(),
        "expired": profile.valid_until < now,
        "created_at": profile.created_at.isoformat(),
    }


async def save_profile(
    session: AsyncSession,
    customer: Customer,
    answers: dict[str, str],
    result: SuitabilityResult,
    created_by_user_id: int | None,
) -> RiskProfile:
    """Persist a new profile version; previous versions become inactive."""
    policy = get_policy()
    last_version = (
        await session.execute(
            select(func.max(RiskProfile.version)).where(RiskProfile.customer_id == customer.id)
        )
    ).scalar_one()
    await session.execute(
        update(RiskProfile).where(RiskProfile.customer_id == customer.id).values(is_active=False)
    )
    profile = RiskProfile(
        customer_id=customer.id,
        version=int(last_version or 0) + 1,
        questionnaire_version=QUESTIONNAIRE_VERSION,
        answers=answers,
        capacity_score=result.capacity_score,
        tolerance_score=result.tolerance_score,
        knowledge_score=result.knowledge_score,
        capacity_level=result.capacity_level,
        tolerance_level=result.tolerance_level,
        risk_level=result.risk_level,
        warnings=[w.to_dict() for w in result.warnings]
        + [{"code": c["code"], "message": c["message"], "blocking": False} for c in result.caps],
        explanation=result.explanation,
        is_active=True,
        valid_until=utcnow() + timedelta(days=policy.profile_validity_days),
        created_by_user_id=created_by_user_id,
    )
    session.add(profile)
    # Müşteri kaydındaki beyan toleransını da güncel tut (eski akışlarla uyum).
    customer.declared_risk_tolerance = max(1, min(5, round(result.risk_level / 2)))
    await session.flush()
    return profile


async def active_profile(session: AsyncSession, customer_id: int) -> RiskProfile | None:
    return (
        await session.execute(
            select(RiskProfile)
            .where(RiskProfile.customer_id == customer_id, RiskProfile.is_active.is_(True))
            .order_by(RiskProfile.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def profile_history(session: AsyncSession, customer_id: int) -> list[RiskProfile]:
    return list(
        (
            await session.execute(
                select(RiskProfile)
                .where(RiskProfile.customer_id == customer_id)
                .order_by(RiskProfile.version.desc())
            )
        )
        .scalars()
        .all()
    )


def legacy_level(customer: Customer) -> int:
    """Estimate a 1–10 level from the legacy 0–100 dynamic risk score."""
    score = RiskService().assess(customer).score
    return max(1, min(10, int(score // 10) + 1))


async def effective_level(session: AsyncSession, customer: Customer) -> EffectiveLevel:
    """Level used for advice: the valid profile, otherwise a legacy estimate."""
    policy = get_policy()
    profile = await active_profile(session, customer.id)
    now = utcnow()
    if profile is not None:
        expired = profile.valid_until < now
        renewal_due = profile.valid_until - now < timedelta(days=policy.renewal_warning_days)
        return EffectiveLevel(
            level=profile.risk_level,
            label=policy.risk_label(profile.risk_level),
            source="profil",
            profile_id=profile.id,
            valid_until=profile.valid_until,
            expired=expired,
            renewal_due=renewal_due,
        )
    level = legacy_level(customer)
    return EffectiveLevel(
        level=level,
        label=policy.risk_label(level),
        source="tahmini",
        profile_id=None,
        valid_until=None,
        expired=False,
        renewal_due=True,
    )


__all__ = [
    "EffectiveLevel",
    "active_profile",
    "effective_level",
    "legacy_level",
    "profile_history",
    "profile_to_dict",
    "save_profile",
]
