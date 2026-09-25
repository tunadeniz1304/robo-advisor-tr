"""Rebalance proposals: draft → pending approval → approved → executed.

State machine (``models.execution``)::

    TASLAK ─► ONAY_BEKLIYOR ─► ONAYLANDI ─► YURUTULDU
                  │                 │
                  ├─► REDDEDILDI    └─► BASARISIZ
                  └─► SURESI_DOLDU (TTL, price move, superseded)

Guarantees:
    * **Idempotency** — a creation key returns the same proposal; approving
      twice (same or no key) never executes the orders twice.
    * **One writer per portfolio** — an in-process lock per portfolio plus
      conditional ``UPDATE … WHERE status = …`` transitions (safe across
      processes: only one transaction can move a row out of a state).
    * **Freshness** — a proposal expires after its TTL or when prices moved
      more than the policy tolerance since it was computed.
    * **LLM independence** — the rationale comes from the gateway, which
      falls back to the deterministic renderer; it can never trigger or
      repeat an execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import numpy as np
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from core.database import session_factory
from core.locks import MemoryLockManager, RedisLockManager
from core.logging import get_logger
from core.metrics import REBALANCE_TOTAL
from core.money import to_decimal
from core.policy import InvestmentPolicy, get_policy
from llm.gateway import LLMGateway
from llm.prompts import DISCLAIMER
from models import AdvisorRun, BLView, Customer, Portfolio, RebalanceProposal
from models.base import utcnow
from models.execution import (
    OPEN_PROPOSAL_STATES,
    PROPOSAL_APPROVED,
    PROPOSAL_EXECUTED,
    PROPOSAL_EXPIRED,
    PROPOSAL_FAILED,
    PROPOSAL_PENDING,
    PROPOSAL_REJECTED,
)
from services.audit import record_audit
from services.explain import build_cards
from services.ledger import LedgerError
from services.market_data.service import MarketDataService
from services.optimization.estimators import expected_returns, ledoit_wolf
from services.optimization.service import OptimizationRequest, OptimizationService, View
from services.rebalancing.broker import SimulatedBroker
from services.rebalancing.costs import TaxModel
from services.rebalancing.engine import CASH, PortfolioState, plan_trades
from services.suitability.service import effective_level
from services.tax_lots import open_lots

logger = get_logger("otonom.proposals")


class ProposalError(Exception):
    """Business error with an HTTP status hint."""

    def __init__(self, message: str, status_code: int = 409, detail: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail if detail is not None else message


@dataclass
class ProposalOutcome:
    """Result of a proposal request."""

    proposal: RebalanceProposal | None
    needs_rebalance: bool
    drift: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    reused: bool = False


def proposal_to_dict(p: RebalanceProposal) -> dict[str, Any]:
    """Public representation of a proposal."""
    return {
        "id": p.id,
        "portfolio_id": p.portfolio_id,
        "customer_id": p.customer_id,
        "status": p.status,
        "source": p.source,
        "trigger": p.trigger,
        "model_level": p.model_level,
        "method": (p.optimizer or {}).get("method"),
        "method_label": (p.optimizer or {}).get("method_label"),
        "target_weights": p.target_weights,
        "before_weights": p.before_weights,
        "after_weights": p.after_weights,
        "orders": p.orders,
        "prices": p.prices,
        "estimated_cost": round(float(p.estimated_cost), 2),
        "estimated_tax": round(float(p.estimated_tax), 2),
        "turnover": round(p.turnover, 6),
        "risk_before": p.risk_before,
        "risk_after": p.risk_after,
        "optimizer": p.optimizer,
        "explanation": p.explanation,
        "suitability": p.suitability,
        "rationale": p.rationale,
        "llm_mode": p.llm_mode,
        "graph_thread_id": p.graph_thread_id,
        "expires_at": p.expires_at.isoformat(),
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "approved_at": p.approved_at.isoformat() if p.approved_at else None,
        "executed_at": p.executed_at.isoformat() if p.executed_at else None,
        "rejected_reason": p.rejected_reason,
        "execution_report": p.execution_report,
        "disclaimer": DISCLAIMER,
    }


class ProposalService:
    """Creates, approves, executes and expires rebalance proposals."""

    def __init__(
        self,
        market: MarketDataService,
        optimizer: OptimizationService,
        gateway: LLMGateway,
        policy: InvestmentPolicy | None = None,
        broker: SimulatedBroker | None = None,
        locks: MemoryLockManager | RedisLockManager | None = None,
    ) -> None:
        self._market = market
        self._optimizer = optimizer
        self._gateway = gateway
        self._p = policy or get_policy()
        self._broker = broker or SimulatedBroker(self._p)
        self._locks = locks or MemoryLockManager()

    # -- helpers -----------------------------------------------------------------

    def _lock(self, portfolio_id: int) -> Any:
        """Portfolio write lock (in-process or shared via Redis)."""
        return self._locks.lock(f"portfolio:{portfolio_id}")

    async def current_prices(self, symbols: list[str]) -> dict[str, float]:
        """Latest prices (panel last row, snapshot fallback per symbol)."""
        wanted = sorted({s for s in symbols if s and s != CASH})
        prices: dict[str, float] = {}
        if not wanted:
            return prices
        panel = await self._market.history(wanted)
        if not panel.empty:
            last = panel.ffill().iloc[-1]
            prices = {s: float(last[s]) for s in panel.columns if s in last and last[s] == last[s]}
        missing = [s for s in wanted if s not in prices]
        if missing:
            snaps = await self._market.fetch_snapshots(missing)
            prices.update({s: snap.last_price for s, snap in snaps.items()})
        return prices

    async def risk_metrics(self, weights: dict[str, float]) -> dict[str, float]:
        """Expected return and volatility of a weight vector (cash earns rf)."""
        rf = self._market.risk_free_rate()
        cash_w = float(weights.get(CASH, 0.0))
        symbols = [s for s, w in weights.items() if s != CASH and w > 1e-6]
        if not symbols:
            return {"volatility": 0.0, "expected_return": round(rf * cash_w, 6)}
        rets = (await self._market.returns(symbols)).tail(252 * 5).dropna()
        cols = [s for s in symbols if s in rets.columns]
        if rets.shape[0] < 30 or not cols:
            return {"volatility": 0.0, "expected_return": round(rf * cash_w, 6)}
        cov, _ = ledoit_wolf(rets[cols])
        mu = expected_returns(rets[cols], cov=cov)
        w = np.array([weights[s] for s in cols])
        vol = float(np.sqrt(max(w @ cov @ w, 0.0)))
        return {
            "volatility": round(vol, 6),
            "expected_return": round(float(w @ mu) + rf * cash_w, 6),
        }

    async def approved_views(self) -> list[View]:
        async with session_factory() as session:
            rows = (
                (await session.execute(select(BLView).where(BLView.status == "onaylandi")))
                .scalars()
                .all()
            )
        now = utcnow()
        return [
            View(r.symbol, r.expected_return, r.confidence, r.rationale)
            for r in rows
            if r.valid_until is None or r.valid_until > now
        ]

    # -- create ---------------------------------------------------------------------

    async def create(
        self,
        portfolio_id: int,
        *,
        actor: str = "system",
        actor_role: str | None = None,
        actor_user_id: int | None = None,
        source: str = "manual",
        trigger: str = "manual",
        method: str | None = None,
        idempotency_key: str | None = None,
        override_ack: bool = False,
        force: bool = False,
        regime: dict[str, Any] | None = None,
        graph_thread_id: str | None = None,
        optimization: dict[str, Any] | None = None,
    ) -> ProposalOutcome:
        """Compute and persist a proposal awaiting approval.

        Raises:
            ProposalError: 404 unknown portfolio, 403 unsuitable allocation.
        """
        async with session_factory() as session:
            if idempotency_key:
                existing = (
                    await session.execute(
                        select(RebalanceProposal).where(
                            RebalanceProposal.idempotency_key == idempotency_key
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    return ProposalOutcome(existing, True, reused=True)
            portfolio = await session.get(Portfolio, portfolio_id)
            if portfolio is None:
                raise ProposalError(f"Portfolio {portfolio_id} bulunamadı.", 404)
            customer = await session.get(Customer, portfolio.customer_id)
            assert customer is not None
            level = await effective_level(session, customer)
            quantities = {
                k: float(v) for k, v in (portfolio.holdings or {}).items() if float(v) > 0
            }
            cash = float(portfolio.cash)

        tilt = float((regime or {}).get("tilt", 0.0))
        if optimization is None:
            result = await self._optimizer.optimize(
                OptimizationRequest(
                    level=level.level,
                    method=method,
                    override_ack=override_ack,
                    views=await self.approved_views(),
                    regime_tilt=tilt,
                )
            )
            if not result.gate.allowed:
                raise ProposalError(
                    "Önerilen dağılım risk seviyeniz için uygun değildir.",
                    403,
                    {"violations": result.gate.violations},
                )
            optimization = result.to_dict()
        target = {k: float(v) for k, v in optimization["weights"].items()}

        prices = await self.current_prices(list(target) + list(quantities))
        state = PortfolioState(cash=cash, quantities=quantities, prices=prices)
        plan = plan_trades(state, target, policy=self._p, force=force)
        if not plan.orders:
            return ProposalOutcome(
                None,
                False,
                plan.drift,
                "Portföy hedef bantlarının içinde; yeniden dengeleme gerekmiyor.",
            )

        # Vergi tahmini (satışlar, lot bazlı)
        tax_model = TaxModel(self._p)
        total_tax = 0.0
        async with session_factory() as session:
            for order in plan.orders:
                if order["side"] != "SELL":
                    continue
                est = tax_model.estimate_sale(
                    order["symbol"],
                    order["quantity"],
                    order["price"],
                    await open_lots(session, portfolio_id, order["symbol"]),
                )
                order["tax"] = est.to_dict()
                total_tax += est.tax

        risk_before = await self.risk_metrics(plan.before_weights)
        risk_after = await self.risk_metrics(plan.after_weights)
        costs = {
            "estimated_cost": round(plan.estimated_cost, 2),
            "estimated_tax": round(total_tax, 2),
            "turnover": round(plan.turnover, 4),
        }
        cards = build_cards(
            level=level.to_dict(),
            optimization=optimization,
            risk_before=risk_before,
            risk_after=risk_after,
            costs=costs,
            regime=regime,
        )
        top_rc = sorted(
            (optimization.get("class_risk_contributions") or {}).items(),
            key=lambda kv: kv[1],
            reverse=True,
        )
        gen: Any = await self._gateway.generate(
            "rebalance_rationale",
            {
                "risk_seviyesi": level.level,
                "risk_kategorisi": level.label,
                "yontem": optimization.get("method_label"),
                "hedef_agirliklar": {
                    k: round(v, 4) for k, v in sorted(target.items(), key=lambda kv: -kv[1])[:8]
                },
                "emirler": [
                    {"sembol": o["symbol"], "yon": o["side"], "tutar": o["amount"]}
                    for o in plan.orders
                ],
                "beklenen_getiri": risk_after["expected_return"],
                "beklenen_volatilite": risk_after["volatility"],
                "onceki_volatilite": risk_before["volatility"],
                "en_buyuk_risk_katkisi": {"sembol": top_rc[0][0], "pay": round(top_rc[0][1], 4)}
                if top_rc
                else None,
                "toplam_maliyet": costs["estimated_cost"],
                "toplam_vergi": costs["estimated_tax"],
            },
        )
        rationale = "\n".join(
            [
                gen.data.ozet,
                *[f"• {g}" for g in gen.data.gerekceler],
                *[f"⚠ {r}" for r in gen.data.riskler],
            ]
        )

        ttl = float(self._p.rebalance.get("proposal_ttl_hours", 24))
        async with self._lock(portfolio_id):
            async with session_factory() as session:
                await session.execute(
                    update(RebalanceProposal)
                    .where(
                        RebalanceProposal.portfolio_id == portfolio_id,
                        RebalanceProposal.status.in_(OPEN_PROPOSAL_STATES[:2]),
                    )
                    .values(
                        status=PROPOSAL_EXPIRED, rejected_reason="Yeni öneriyle geçersiz kılındı."
                    )
                )
                proposal = RebalanceProposal(
                    portfolio_id=portfolio_id,
                    customer_id=customer.id,
                    status=PROPOSAL_PENDING,
                    idempotency_key=idempotency_key,
                    source=source,
                    trigger=trigger,
                    model_level=int(optimization.get("model_level", level.level)),
                    target_weights={k: round(v, 6) for k, v in target.items()},
                    before_weights={k: round(v, 6) for k, v in plan.before_weights.items()},
                    after_weights={k: round(v, 6) for k, v in plan.after_weights.items()},
                    orders=plan.orders,
                    prices={s: prices[s] for s in {o["symbol"] for o in plan.orders}},
                    estimated_cost=to_decimal(plan.estimated_cost),
                    estimated_tax=to_decimal(total_tax),
                    turnover=plan.turnover,
                    risk_before=risk_before,
                    risk_after=risk_after,
                    optimizer={
                        k: optimization[k]
                        for k in (
                            "method",
                            "method_label",
                            "params",
                            "expected_return",
                            "volatility",
                            "sharpe",
                            "cvar_95",
                            "risk_free_rate",
                            "class_weights",
                            "model_class_weights",
                            "class_risk_contributions",
                            "risk_contributions",
                            "bl",
                        )
                        if k in optimization
                    },
                    explanation=cards,
                    suitability={
                        **level.to_dict(),
                        "gate": optimization.get("gate"),
                        "override_ack": override_ack,
                    },
                    rationale=rationale,
                    llm_mode=gen.mode,
                    graph_thread_id=graph_thread_id,
                    created_by_user_id=actor_user_id,
                    expires_at=utcnow() + timedelta(hours=ttl),
                    execution_report={"drift": plan.drift, "solver": plan.solver},
                )
                session.add(proposal)
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    existing = (
                        await session.execute(
                            select(RebalanceProposal).where(
                                RebalanceProposal.idempotency_key == idempotency_key
                            )
                        )
                    ).scalar_one()
                    return ProposalOutcome(existing, True, reused=True)
                await session.refresh(proposal)
        REBALANCE_TOTAL.labels(event="proposed").inc()
        await record_audit(
            actor=actor,
            actor_role=actor_role,
            action="proposal.created",
            entity_type="rebalance_proposal",
            entity_id=proposal.id,
            customer_id=customer.id,
            payload={
                "portfolio_id": portfolio_id,
                "source": source,
                "trigger": trigger,
                "orders": len(plan.orders),
                "method": optimization.get("method"),
                "llm_mode": gen.mode,
                "override_ack": override_ack,
            },
        )
        logger.info(
            "proposal_created", proposal_id=proposal.id, orders=len(plan.orders), llm_mode=gen.mode
        )
        return ProposalOutcome(proposal, True, plan.drift)

    async def create_custom(
        self,
        portfolio_id: int,
        buys: dict[str, float],
        *,
        source: str,
        trigger: str,
        rationale: str,
        actor: str = "system",
    ) -> RebalanceProposal:
        """Persist a simple buy-only proposal (e.g. Autopilot cash sweep)."""
        prices = await self.current_prices(list(buys))
        costs = self._broker._costs  # noqa: SLF001 - aynı maliyet modeli
        orders = []
        total_cost = 0.0
        for sym, amount in buys.items():
            price = prices[sym]
            qty = round(amount / price, 6)
            cost = costs.estimate(sym, qty * price)
            total_cost += cost.total
            orders.append(
                {
                    "symbol": sym,
                    "ticker": sym,
                    "side": "BUY",
                    "quantity": qty,
                    "price": price,
                    "amount": round(qty * price, 2),
                    "cost": cost.to_dict(),
                }
            )
        async with self._lock(portfolio_id):
            async with session_factory() as session:
                portfolio = await session.get(Portfolio, portfolio_id)
                if portfolio is None:
                    raise ProposalError(f"Portfolio {portfolio_id} bulunamadı.", 404)
                proposal = RebalanceProposal(
                    portfolio_id=portfolio_id,
                    customer_id=portfolio.customer_id,
                    status=PROPOSAL_PENDING,
                    source=source,
                    trigger=trigger,
                    orders=orders,
                    prices={s: prices[s] for s in buys},
                    estimated_cost=to_decimal(total_cost),
                    rationale=rationale,
                    llm_mode="demo",
                    expires_at=utcnow()
                    + timedelta(hours=float(self._p.rebalance.get("proposal_ttl_hours", 24))),
                )
                session.add(proposal)
                await session.commit()
                await session.refresh(proposal)
        REBALANCE_TOTAL.labels(event="proposed").inc()
        await record_audit(
            actor=actor,
            action="proposal.created",
            entity_type="rebalance_proposal",
            entity_id=proposal.id,
            customer_id=proposal.customer_id,
            payload={"source": source, "trigger": trigger, "orders": len(orders)},
        )
        return proposal

    # -- approve / reject ---------------------------------------------------------------

    async def get(self, proposal_id: int) -> RebalanceProposal:
        async with session_factory() as session:
            proposal = await session.get(RebalanceProposal, proposal_id)
        if proposal is None:
            raise ProposalError(f"Öneri {proposal_id} bulunamadı.", 404)
        return proposal

    async def _set_status(self, proposal_id: int, status: str, reason: str | None = None) -> None:
        async with session_factory() as session:
            await session.execute(
                update(RebalanceProposal)
                .where(RebalanceProposal.id == proposal_id)
                .values(status=status, rejected_reason=reason)
            )
            await session.commit()

    async def approve(
        self,
        proposal_id: int,
        *,
        user_id: int | None,
        role: str | None = None,
        idempotency_key: str | None = None,
    ) -> RebalanceProposal:
        """Move ``ONAY_BEKLIYOR → ONAYLANDI`` exactly once.

        Raises:
            ProposalError: 409 when not pending/expired or prices moved.
        """
        proposal = await self.get(proposal_id)
        if proposal.status in (PROPOSAL_APPROVED, PROPOSAL_EXECUTED):
            if idempotency_key and proposal.approval_idempotency_key not in (None, idempotency_key):
                raise ProposalError("Öneri farklı bir istekle zaten onaylandı.", 409)
            return proposal  # idempotent: ikinci onay yeniden yürütmez
        if proposal.status != PROPOSAL_PENDING:
            raise ProposalError(f"Öneri onaylanamaz (durum: {proposal.status}).", 409)
        if proposal.expires_at < utcnow():
            await self._set_status(proposal_id, PROPOSAL_EXPIRED, "Onay süresi doldu.")
            REBALANCE_TOTAL.labels(event="expired").inc()
            raise ProposalError("Önerinin süresi doldu; lütfen yeniden hesaplayın.", 409)
        tolerance = float(self._p.rebalance.get("price_tolerance", 0.03))
        now_prices = await self.current_prices(list(proposal.prices))
        moved = {
            s: round(now_prices[s] / float(p0) - 1.0, 4)
            for s, p0 in proposal.prices.items()
            if s in now_prices
            and float(p0) > 0
            and abs(now_prices[s] / float(p0) - 1.0) > tolerance
        }
        if moved:
            await self._set_status(
                proposal_id, PROPOSAL_EXPIRED, "Fiyatlar tolerans dışında değişti."
            )
            REBALANCE_TOTAL.labels(event="expired").inc()
            raise ProposalError(
                "Fiyatlar öneri hesaplandığından beri değişti; öneri yeniden hesaplanmalı.",
                409,
                {"moved": moved},
            )
        async with session_factory() as session:
            res = await session.execute(
                update(RebalanceProposal)
                .where(
                    RebalanceProposal.id == proposal_id,
                    RebalanceProposal.status == PROPOSAL_PENDING,
                )
                .values(
                    status=PROPOSAL_APPROVED,
                    approved_by_user_id=user_id,
                    approved_at=utcnow(),
                    approval_idempotency_key=idempotency_key,
                )
            )
            await session.commit()
        if getattr(res, "rowcount", 0) == 1:
            REBALANCE_TOTAL.labels(event="approved").inc()
            await record_audit(
                actor=f"user:{user_id}" if user_id else "system",
                actor_role=role,
                action="proposal.approved",
                entity_type="rebalance_proposal",
                entity_id=proposal_id,
                customer_id=proposal.customer_id,
                payload={"idempotency_key": idempotency_key},
            )
        return await self.get(proposal_id)

    async def reject(
        self, proposal_id: int, *, user_id: int | None, role: str | None, reason: str
    ) -> RebalanceProposal:
        proposal = await self.get(proposal_id)
        if proposal.status == PROPOSAL_REJECTED:
            return proposal
        if proposal.status not in OPEN_PROPOSAL_STATES[:2]:
            raise ProposalError(f"Öneri reddedilemez (durum: {proposal.status}).", 409)
        async with session_factory() as session:
            await session.execute(
                update(RebalanceProposal)
                .where(
                    RebalanceProposal.id == proposal_id,
                    RebalanceProposal.status.in_(OPEN_PROPOSAL_STATES[:2]),
                )
                .values(status=PROPOSAL_REJECTED, rejected_reason=reason)
            )
            await session.commit()
        REBALANCE_TOTAL.labels(event="rejected").inc()
        await record_audit(
            actor=f"user:{user_id}" if user_id else "system",
            actor_role=role,
            action="proposal.rejected",
            entity_type="rebalance_proposal",
            entity_id=proposal_id,
            customer_id=proposal.customer_id,
            payload={"reason": reason},
        )
        return await self.get(proposal_id)

    # -- execute ------------------------------------------------------------------------

    async def execute(self, proposal_id: int, *, actor: str = "system") -> RebalanceProposal:
        """Fill an approved proposal exactly once (``ONAYLANDI → YURUTULDU``)."""
        proposal = await self.get(proposal_id)
        async with self._lock(proposal.portfolio_id):
            async with session_factory() as session:
                proposal = await session.get(RebalanceProposal, proposal_id)  # type: ignore[assignment]  # re-read inside the lock; None handled below
                assert proposal is not None
                if proposal.status == PROPOSAL_EXECUTED:
                    return proposal
                if proposal.status != PROPOSAL_APPROVED:
                    raise ProposalError(f"Öneri yürütülemez (durum: {proposal.status}).", 409)
                portfolio = await session.get(Portfolio, proposal.portfolio_id)
                assert portfolio is not None
                orders = list(proposal.orders)
                customer_id = proposal.customer_id
                prices = await self.current_prices([o["symbol"] for o in orders])
                try:
                    report = await self._broker.execute(
                        session, portfolio, orders, prices, proposal_id=proposal_id
                    )
                except LedgerError as exc:
                    await session.rollback()
                    async with session_factory() as s2:
                        await s2.execute(
                            update(RebalanceProposal)
                            .where(RebalanceProposal.id == proposal_id)
                            .values(status=PROPOSAL_FAILED, execution_report={"error": str(exc)})
                        )
                        await s2.commit()
                    REBALANCE_TOTAL.labels(event="failed").inc()
                    await record_audit(
                        actor=actor,
                        action="proposal.failed",
                        entity_type="rebalance_proposal",
                        entity_id=proposal_id,
                        customer_id=customer_id,
                        payload={"error": str(exc)},
                    )
                    return await self.get(proposal_id)
                res = await session.execute(
                    update(RebalanceProposal)
                    .where(
                        RebalanceProposal.id == proposal_id,
                        RebalanceProposal.status == PROPOSAL_APPROVED,
                    )
                    .values(
                        status=PROPOSAL_EXECUTED,
                        executed_at=utcnow(),
                        execution_report={**(proposal.execution_report or {}), **report.to_dict()},
                    )
                    .execution_options(synchronize_session=False)
                )
                if getattr(res, "rowcount", 0) != 1:
                    await session.rollback()  # başka bir işlem önce yürüttü
                    return await self.get(proposal_id)
                session.add(
                    AdvisorRun(
                        portfolio_id=proposal.portfolio_id,
                        customer_id=customer_id,
                        market_input={"prices": prices},
                        target_weights=proposal.target_weights,
                        orders=report.fills,
                        report=proposal.rationale,
                        status="success" if proposal.llm_mode != "fallback" else "degraded",
                        error=None,
                    )
                )
                await session.commit()
        REBALANCE_TOTAL.labels(event="executed").inc()
        await record_audit(
            actor=actor,
            action="proposal.executed",
            entity_type="rebalance_proposal",
            entity_id=proposal_id,
            customer_id=customer_id,
            payload={
                "fills": len(report.fills),
                "fees": round(report.total_fees, 2),
                "tax": round(report.total_tax, 2),
            },
        )
        logger.info("proposal_executed", proposal_id=proposal_id, fills=len(report.fills))
        return await self.get(proposal_id)

    # -- housekeeping ----------------------------------------------------------------------

    async def expire_stale(self) -> int:
        """Expire open proposals past their TTL (scheduler job)."""
        async with session_factory() as session:
            res = await session.execute(
                update(RebalanceProposal)
                .where(
                    RebalanceProposal.status.in_(OPEN_PROPOSAL_STATES[:2]),
                    RebalanceProposal.expires_at < utcnow(),
                )
                .values(status=PROPOSAL_EXPIRED, rejected_reason="Onay süresi doldu.")
            )
            await session.commit()
        count = int(getattr(res, "rowcount", 0) or 0)
        if count:
            REBALANCE_TOTAL.labels(event="expired").inc(count)
        return count


__all__ = ["ProposalError", "ProposalOutcome", "ProposalService", "proposal_to_dict"]
