"""Customer CRUD router (async FastAPI)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_session
from models import Customer
from schemas.customer import CustomerCreate, CustomerRead, CustomerUpdate

router = APIRouter(prefix="/customers", tags=["customers"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def _get_or_404(session: AsyncSession, customer_id: int) -> Customer:
    """Fetch a customer by id or raise 404 (single source of truth)."""
    customer = await session.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Customer {customer_id} bulunamadı.",
        )
    return customer


@router.post("", response_model=CustomerRead, status_code=status.HTTP_201_CREATED)
async def create_customer(payload: CustomerCreate, session: SessionDep) -> Customer:
    """Create a new customer (Müşteri)."""
    customer = Customer(**payload.model_dump())
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
    return customer


@router.get("", response_model=list[CustomerRead])
async def list_customers(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Customer]:
    """List customers with pagination."""
    result = await session.execute(
        select(Customer).order_by(Customer.id).limit(limit).offset(offset)
    )
    return list(result.scalars().all())


@router.get("/{customer_id}", response_model=CustomerRead)
async def get_customer(customer_id: int, session: SessionDep) -> Customer:
    """Fetch a single customer by id."""
    return await _get_or_404(session, customer_id)


@router.put("/{customer_id}", response_model=CustomerRead)
async def update_customer(
    customer_id: int, payload: CustomerUpdate, session: SessionDep
) -> Customer:
    """Update customer fields (only provided fields are changed)."""
    customer = await _get_or_404(session, customer_id)
    updates = payload.model_dump(exclude_unset=True)
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
    return customer


@router.delete("/{customer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_customer(customer_id: int, session: SessionDep) -> None:
    """Delete a customer and its cascaded portfolios/transactions."""
    customer = await _get_or_404(session, customer_id)
    await session.delete(customer)
    await session.commit()
