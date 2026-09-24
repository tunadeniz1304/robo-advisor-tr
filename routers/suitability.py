"""Suitability router: questionnaire, risk profile submission and history."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from core.deps import SessionDep, UserDep, actor_of, load_customer_checked
from services.audit import record_audit
from services.suitability.questionnaire import questionnaire_payload, validate_answers
from services.suitability.scoring import score_answers
from services.suitability.service import (
    effective_level,
    profile_history,
    profile_to_dict,
    save_profile,
)

router = APIRouter(tags=["suitability"])


class ProfileSubmission(BaseModel):
    """Answers of the suitability questionnaire."""

    answers: dict[str, str] = Field(description="{soru_id: seçenek}")
    confirm_inconsistencies: bool = Field(
        default=False,
        description="Tutarsızlık uyarılarını okuyup cevaplarını onaylıyorum.",
    )


@router.get("/suitability/questionnaire", summary="SPK uygunluk anketi")
async def questionnaire(user: UserDep) -> dict[str, Any]:
    """Questionnaire definition (15 questions in 5 sections)."""
    return questionnaire_payload()


@router.post(
    "/customers/{customer_id}/risk-profile",
    status_code=status.HTTP_201_CREATED,
    summary="Uygunluk testini gönder ve risk profilini oluştur",
)
async def submit_profile(
    customer_id: int, payload: ProfileSubmission, session: SessionDep, user: UserDep
) -> dict[str, Any]:
    """Score the answers; contradictory answers must be re-answered or confirmed."""
    customer = await load_customer_checked(session, user, customer_id)
    checked = validate_answers(payload.answers)
    if not checked.ok:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Anket eksik veya geçersiz.",
                "missing": checked.missing,
                "invalid": checked.invalid,
            },
        )
    result = score_answers(checked.answers)
    if result.needs_confirmation and not payload.confirm_inconsistencies:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Cevaplarınızda tutarsızlık var. İlgili soruları yeniden yanıtlayın veya onaylayın.",
                "reask": sorted({q for w in result.warnings if w.blocking for q in w.questions}),
                "result": result.to_dict(),
            },
        )
    profile = await save_profile(session, customer, checked.answers, result, user.id)
    await session.commit()
    await record_audit(
        actor=actor_of(user),
        actor_role=user.role,
        action="suitability.profile",
        entity_type="risk_profile",
        entity_id=profile.id,
        customer_id=customer.id,
        payload={
            "version": profile.version,
            "risk_level": profile.risk_level,
            "warnings": [w.code for w in result.warnings],
            "confirmed_inconsistencies": payload.confirm_inconsistencies,
        },
    )
    return {"profile": profile_to_dict(profile), "result": result.to_dict()}


@router.get("/customers/{customer_id}/risk-profile", summary="Güncel risk profili ve geçmiş")
async def get_profile(customer_id: int, session: SessionDep, user: UserDep) -> dict[str, Any]:
    """Current effective level (profile or legacy estimate) and all versions."""
    customer = await load_customer_checked(session, user, customer_id)
    level = await effective_level(session, customer)
    history = await profile_history(session, customer.id)
    return {
        "effective": level.to_dict(),
        "current": profile_to_dict(history[0]) if history else None,
        "history": [profile_to_dict(p) for p in history],
        "needs_profile": level.source != "profil" or level.expired,
    }
