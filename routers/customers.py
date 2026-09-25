"""Customer CRUD router (async FastAPI, role and ownership aware)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from core.deps import (
    CurrentUser,
    SessionDep,
    UserDep,
    actor_of,
    load_customer_checked,
    require_roles,
)
from models import Customer
from schemas.customer import CustomerCreate, CustomerRead, CustomerUpdate
from services.audit import record_audit

router = APIRouter(prefix="/customers", tags=["customers"])

StaffDep = Annotated[CurrentUser, require_roles("admin", "danisman")]
AdminDep = Annotated[CurrentUser, require_roles("admin")]


@router.post("", response_model=CustomerRead, status_code=status.HTTP_201_CREATED)
async def create_customer(payload: CustomerCreate, session: SessionDep, user: StaffDep) -> Customer:
    """Create a new customer (danışman: automatically assigned to self)."""
    data = payload.model_dump()
    if user.is_advisor:
        data["advisor_user_id"] = user.id
    customer = Customer(**data)
    session.add(customer)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bu e-posta adresi zaten kayıtlı.",
        ) from exc
    await session.refresh(customer)
    await record_audit(
        actor=actor_of(user),
        actor_role=user.role,
        action="customer.create",
        entity_type="customer",
        entity_id=customer.id,
        customer_id=customer.id,
    )
    return customer


@router.get("", response_model=list[CustomerRead])
async def list_customers(
    session: SessionDep,
    user: UserDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Customer]:
    """List customers visible to the caller (paginated)."""
    stmt = select(Customer).order_by(Customer.id)
    if user.is_advisor:
        stmt = stmt.where(Customer.advisor_user_id == user.id)
    elif user.is_customer:
        stmt = stmt.where(Customer.id == (user.customer_id or -1))
    result = await session.execute(stmt.limit(limit).offset(offset))
    return list(result.scalars().all())


@router.get("/{customer_id}", response_model=CustomerRead)
async def get_customer(customer_id: int, session: SessionDep, user: UserDep) -> Customer:
    """Fetch a single customer by id."""
    return await load_customer_checked(session, user, customer_id)


@router.put("/{customer_id}", response_model=CustomerRead)
async def update_customer(
    customer_id: int, payload: CustomerUpdate, session: SessionDep, user: UserDep
) -> Customer:
    """Update customer fields (only provided fields are changed)."""
    customer = await load_customer_checked(session, user, customer_id)
    updates = payload.model_dump(exclude_unset=True)
    if "advisor_user_id" in updates and not user.is_admin:
        raise HTTPException(status_code=403, detail="Danışman ataması yalnızca yöneticiye açıktır.")
    for field, value in updates.items():
        setattr(customer, field, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bu e-posta adresi başka bir müşteriye ait.",
        ) from exc
    await session.refresh(customer)
    await record_audit(
        actor=actor_of(user),
        actor_role=user.role,
        action="customer.update",
        entity_type="customer",
        entity_id=customer.id,
        customer_id=customer.id,
        payload={"fields": sorted(k for k in updates if k not in {"email", "monthly_income"})},
    )
    return customer


@router.delete("/{customer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_customer(customer_id: int, session: SessionDep, user: AdminDep) -> None:
    """Delete a customer and its cascaded portfolios/transactions (admin only)."""
    customer = await load_customer_checked(session, user, customer_id)
    await session.delete(customer)
    await session.commit()
    await record_audit(
        actor=actor_of(user),
        actor_role=user.role,
        action="customer.delete",
        entity_type="customer",
        entity_id=customer_id,
    )
