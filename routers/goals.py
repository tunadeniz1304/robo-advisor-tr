"""Goals router: goal CRUD, Monte Carlo simulation and what-if scenarios."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from core.deps import SessionDep, UserDep, actor_of, load_customer_checked
from models import Customer, Goal
from services.audit import record_audit
from services.planning.service import GOAL_TYPES, GoalPlanningService
from services.suitability.service import effective_level

router = APIRouter(tags=["goals"])


class GoalIn(BaseModel):
    goal_type: str = Field(pattern="^(" + "|".join(GOAL_TYPES) + ")$")
    name: str = Field(min_length=2, max_length=120)
    target_amount_real: float = Field(gt=0, le=1e12, description="Bugünkü TL ile hedef tutar")
    horizon_years: float = Field(gt=0, le=50)
    initial_amount: float = Field(default=0.0, ge=0)
    monthly_contribution: float = Field(default=0.0, ge=0)
    priority: int = Field(default=1, ge=1, le=5)
    portfolio_id: int | None = None
    portfolio_share: float | None = Field(default=None, ge=0, le=1)
    risk_level: int | None = Field(default=None, ge=1, le=10)


class GoalUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    target_amount_real: float | None = Field(default=None, gt=0)
    horizon_years: float | None = Field(default=None, gt=0, le=50)
    initial_amount: float | None = Field(default=None, ge=0)
    monthly_contribution: float | None = Field(default=None, ge=0)
    priority: int | None = Field(default=None, ge=1, le=5)
    risk_level: int | None = Field(default=None, ge=1, le=10)


class WhatIfIn(BaseModel):
    horizon_delta_years: float = Field(default=0.0, ge=-30, le=30)
    contribution_multiplier: float = Field(default=1.0, ge=0, le=10)
    contribution_delta: float = Field(default=0.0, ge=-1e9, le=1e9)
    initial_delta: float = Field(default=0.0, ge=-1e12, le=1e12)
    level_delta: int = Field(default=0, ge=-9, le=9)


def planner(request: Request) -> GoalPlanningService:
    return cast(GoalPlanningService, request.app.state.container.planner)


def goal_to_dict(g: Goal) -> dict[str, Any]:
    return {
        "id": g.id,
        "customer_id": g.customer_id,
        "portfolio_id": g.portfolio_id,
        "goal_type": g.goal_type,
        "goal_type_label": GOAL_TYPES.get(g.goal_type, g.goal_type),
        "name": g.name,
        "target_amount_real": float(g.target_amount_real),
        "horizon_years": g.horizon_years,
        "initial_amount": float(g.initial_amount),
        "monthly_contribution": float(g.monthly_contribution),
        "priority": g.priority,
        "portfolio_share": g.portfolio_share,
        "risk_level": g.risk_level,
        "last_simulation": g.last_simulation,
        "created_at": g.created_at.isoformat() if g.created_at else None,
    }


async def _goal_checked(session: SessionDep, user: UserDep, goal_id: int) -> Goal:
    goal = await session.get(Goal, goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail=f"Hedef {goal_id} bulunamadı.")
    await load_customer_checked(session, user, goal.customer_id)
    return goal


async def _level_for(session: SessionDep, goal: Goal) -> int:
    if goal.risk_level:
        return int(goal.risk_level)
    customer = await session.get(Customer, goal.customer_id)
    assert customer is not None
    return (await effective_level(session, customer)).level


@router.post(
    "/customers/{customer_id}/goals", status_code=status.HTTP_201_CREATED, summary="Hedef oluştur"
)
async def create_goal(
    customer_id: int, body: GoalIn, session: SessionDep, user: UserDep
) -> dict[str, Any]:
    await load_customer_checked(session, user, customer_id)
    goal = Goal(
        customer_id=customer_id,
        portfolio_id=body.portfolio_id,
        goal_type=body.goal_type,
        name=body.name,
        target_amount_real=Decimal(str(body.target_amount_real)),
        horizon_years=body.horizon_years,
        initial_amount=Decimal(str(body.initial_amount)),
        monthly_contribution=Decimal(str(body.monthly_contribution)),
        priority=body.priority,
        portfolio_share=body.portfolio_share,
        risk_level=body.risk_level,
    )
    session.add(goal)
    await session.commit()
    await session.refresh(goal)
    await record_audit(
        actor=actor_of(user),
        actor_role=user.role,
        action="goal.create",
        entity_type="goal",
        entity_id=goal.id,
        customer_id=customer_id,
        payload={"goal_type": goal.goal_type},
    )
    return goal_to_dict(goal)


@router.get("/customers/{customer_id}/goals", summary="Hedefler")
async def list_goals(customer_id: int, session: SessionDep, user: UserDep) -> list[dict[str, Any]]:
    await load_customer_checked(session, user, customer_id)
    rows = (
        (
            await session.execute(
                select(Goal).where(Goal.customer_id == customer_id).order_by(Goal.priority, Goal.id)
            )
        )
        .scalars()
        .all()
    )
    return [goal_to_dict(g) for g in rows]


@router.get("/goals/{goal_id}", summary="Hedef detayı")
async def get_goal(goal_id: int, session: SessionDep, user: UserDep) -> dict[str, Any]:
    return goal_to_dict(await _goal_checked(session, user, goal_id))


@router.put("/goals/{goal_id}", summary="Hedefi güncelle")
async def update_goal(
    goal_id: int, body: GoalUpdate, session: SessionDep, user: UserDep
) -> dict[str, Any]:
    goal = await _goal_checked(session, user, goal_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        if (
            key in {"target_amount_real", "initial_amount", "monthly_contribution"}
            and value is not None
        ):
            value = Decimal(str(value))
        setattr(goal, key, value)
    await session.commit()
    await session.refresh(goal)
    return goal_to_dict(goal)


@router.delete("/goals/{goal_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Hedefi sil")
async def delete_goal(goal_id: int, session: SessionDep, user: UserDep) -> None:
    goal = await _goal_checked(session, user, goal_id)
    await session.delete(goal)
    await session.commit()


@router.post("/goals/{goal_id}/simulate", summary="Monte Carlo hedef simülasyonu")
async def simulate_goal(
    request: Request,
    goal_id: int,
    session: SessionDep,
    user: UserDep,
    with_report: bool = Query(default=False, description="LLM/demo özet raporu ekle"),
) -> dict[str, Any]:
    """Success probability, P10/P50/P90 real & nominal bands, required contribution, what-ifs."""
    goal = await _goal_checked(session, user, goal_id)
    level = await _level_for(session, goal)
    svc = planner(request)
    result = await svc.simulate_goal(
        initial=float(goal.initial_amount),
        monthly=float(goal.monthly_contribution),
        target=float(goal.target_amount_real),
        years=goal.horizon_years,
        level=level,
    )
    goal.last_simulation = {
        k: result[k]
        for k in (
            "success_probability",
            "required_monthly_contribution",
            "p10_real",
            "p50_real",
            "p90_real",
            "risk_level",
        )
    }
    await session.commit()
    payload: dict[str, Any] = {"goal": goal_to_dict(goal), "result": result}
    if with_report:
        payload["report"] = await svc.report(goal_to_dict(goal), result)
    return payload


@router.post("/goals/{goal_id}/what-if", summary="Ya olursa? senaryosu")
async def what_if(
    request: Request, goal_id: int, body: WhatIfIn, session: SessionDep, user: UserDep
) -> dict[str, Any]:
    goal = await _goal_checked(session, user, goal_id)
    level = await _level_for(session, goal)
    return await planner(request).what_if(
        initial=float(goal.initial_amount),
        monthly=float(goal.monthly_contribution),
        target=float(goal.target_amount_real),
        years=goal.horizon_years,
        level=level,
        **body.model_dump(),
    )
