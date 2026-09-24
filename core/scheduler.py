"""Scheduled jobs (APScheduler ``AsyncIOScheduler``).

Jobs are plain coroutines (:func:`drift_check_job`, :func:`expire_job`, …)
so tests call them directly; the scheduler only wires the triggers:

* drift check — every weekday 18:30 (Europe/Istanbul); proposals are
  created for portfolios outside their bands (``source=scheduler``).
  The calendar policy (``monthly``/``quarterly``) adds a full check on the
  first business day of the period.
* expire — every 15 minutes, open proposals past their TTL.
* price persistence — daily, recent live closes into ``price_history``.
* extra jobs (autopilot, nudges) can be registered by other modules.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select

from core.database import session_factory
from core.logging import get_logger
from core.policy import get_policy
from models import Portfolio, RebalanceProposal
from models.execution import OPEN_PROPOSAL_STATES

logger = get_logger("otonom.scheduler")

TIMEZONE = "Europe/Istanbul"


async def drift_check_job(container: Any, trigger: str = "drift") -> list[int]:
    """Create proposals for portfolios outside their drift bands."""
    created: list[int] = []
    async with session_factory() as session:
        portfolios = (await session.execute(select(Portfolio))).scalars().all()
        open_ids = set(
            (
                await session.execute(
                    select(RebalanceProposal.portfolio_id).where(
                        RebalanceProposal.status.in_(OPEN_PROPOSAL_STATES)
                    )
                )
            )
            .scalars()
            .all()
        )
    for p in portfolios:
        if p.id in open_ids or (not p.holdings and float(p.cash) <= 0):
            continue
        has_history = bool(p.holdings)
        if not has_history:
            continue  # ilk yatırım müşteri/danışman onboarding akışıyla yapılır
        try:
            outcome = await container.proposals.create(
                p.id, actor="system", source="scheduler", trigger=trigger
            )
        except Exception as exc:  # noqa: BLE001 - tek portföy hatası işi durdurmasın
            logger.warning(
                "drift_job_portfolio_failed", portfolio_id=p.id, error_type=type(exc).__name__
            )
            continue
        if outcome.proposal is not None:
            created.append(outcome.proposal.id)
    logger.info("drift_check_done", created=len(created), trigger=trigger)
    return created


async def expire_job(container: Any) -> int:
    return int(await container.proposals.expire_stale())


async def persist_prices_job(container: Any) -> int:
    return int(await container.market.persist_prices())


def build_scheduler(
    container: Any,
    extra_jobs: list[tuple[str, Callable[[], Awaitable[Any]], dict[str, Any]]] | None = None,
) -> Any:
    """Create (not start) the scheduler with the standard jobs."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    policy = get_policy()
    sched = AsyncIOScheduler(timezone=TIMEZONE)
    sched.add_job(
        drift_check_job,
        "cron",
        day_of_week="mon-fri",
        hour=18,
        minute=30,
        args=[container],
        id="drift_check",
    )
    calendar = str(policy.rebalance.get("calendar", "monthly"))
    months = "1,4,7,10" if calendar == "quarterly" else "*"
    sched.add_job(
        drift_check_job,
        "cron",
        month=months,
        day="1-3",
        day_of_week="mon-fri",
        hour=10,
        args=[container, "calendar"],
        id="calendar_check",
    )
    sched.add_job(expire_job, "interval", minutes=15, args=[container], id="expire_proposals")
    sched.add_job(
        persist_prices_job, "cron", hour=19, minute=0, args=[container], id="persist_prices"
    )
    for job_id, func, trigger_kwargs in extra_jobs or []:
        sched.add_job(func, id=job_id, **trigger_kwargs)
    return sched


__all__ = ["build_scheduler", "drift_check_job", "expire_job", "persist_prices_job"]
