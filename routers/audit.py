"""Audit router: browse the hash-chained audit log and verify its integrity."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from core.deps import CurrentUser, SessionDep, require_roles
from models import AuditLog, Customer
from services.audit import verify_chain

router = APIRouter(prefix="/audit", tags=["audit"])

StaffDep = Annotated[CurrentUser, require_roles("admin", "danisman")]
AdminDep = Annotated[CurrentUser, require_roles("admin")]


class AuditOut(BaseModel):
    id: int
    created_at: datetime
    actor: str
    actor_role: str | None
    action: str
    entity_type: str
    entity_id: str | None
    customer_id: int | None
    payload: dict[str, Any]
    prev_hash: str
    hash: str

    model_config = {"from_attributes": True}


@router.get("", response_model=list[AuditOut], summary="Denetim kayıtları (en yeni önce)")
async def list_audit(
    session: SessionDep,
    user: StaffDep,
    action: str | None = None,
    customer_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[AuditLog]:
    """List audit records; advisors only see their customers' records."""
    stmt = select(AuditLog).order_by(AuditLog.id.desc())
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if customer_id is not None:
        stmt = stmt.where(AuditLog.customer_id == customer_id)
    if user.is_advisor:
        stmt = stmt.join(Customer, Customer.id == AuditLog.customer_id).where(
            Customer.advisor_user_id == user.id
        )
    return list((await session.execute(stmt.limit(limit))).scalars().all())


@router.get("/verify", summary="Hash zincirini doğrula")
async def verify(user: AdminDep) -> dict[str, Any]:
    """Recompute the whole chain; ``valid=false`` pinpoints the first broken row."""
    return (await verify_chain()).to_dict()
