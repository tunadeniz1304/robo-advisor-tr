"""Rebalancing engine, costs, taxes, broker, expiry and scheduler jobs."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from core.policy import get_policy
from services.rebalancing.costs import CostModel, TaxModel, harvest_candidates
from services.rebalancing.engine import CASH, PortfolioState, drift_report, plan_trades
from tests.conftest import create_customer, create_portfolio

TARGET = {"TL_PPF": 0.3, "TL_TAHVIL": 0.2, "ALTIN_TL": 0.15, "XU100.IS": 0.2, "THYAO.IS": 0.15}
PRICES = {
    "TL_PPF": 650.0,
    "TL_TAHVIL": 364.0,
    "ALTIN_TL": 6767.0,
    "XU100.IS": 12888.0,
    "THYAO.IS": 288.5,
    "OLD.IS": 10.0,
}


def _at_target(cash: float = 1000.0, total: float = 100_000.0) -> dict[str, float]:
    return {s: w * total / PRICES[s] for s, w in TARGET.items()}


# ------------------------------------------------------------------ engine


def test_within_band_portfolio_has_no_orders() -> None:
    state = PortfolioState(cash=1000.0, quantities=_at_target(), prices=PRICES)
    assert drift_report(state, TARGET)["needs_rebalance"] is False
    plan = plan_trades(state, TARGET)
    assert plan.needs_rebalance is False and plan.orders == []


def test_all_cash_is_invested_into_bands() -> None:
    state = PortfolioState(cash=100_000.0, quantities={}, prices=PRICES)
    plan = plan_trades(state, TARGET)
    assert plan.solver == "min_turnover_lp"
    assert all(o["side"] == "BUY" for o in plan.orders)
    policy = get_policy()
    for sym, tgt in TARGET.items():
        band = policy.band_for(
            __import__("services.market_data.universe", fromlist=["x"]).asset_class_of(sym)
        )
        assert abs(plan.after_weights.get(sym, 0.0) - tgt) <= band + 1e-6
    assert plan.after_weights[CASH] <= policy.rebalance["max_cash_weight"]


def test_cash_first_no_sells_when_new_cash_fixes_drift() -> None:
    q = _at_target()
    q["THYAO.IS"] *= 1.6  # hisse fazla kilolu ama bant içinde
    state = PortfolioState(cash=20_000.0, quantities=q, prices=PRICES)
    plan = plan_trades(state, TARGET)
    assert plan.orders and all(o["side"] == "BUY" for o in plan.orders)


def test_out_of_universe_holding_is_sold_and_bands_restored() -> None:
    q = _at_target()
    q["OLD.IS"] = 1000.0
    q["THYAO.IS"] *= 2.5  # bant dışı
    state = PortfolioState(cash=0.0, quantities=q, prices=PRICES)
    report = drift_report(state, TARGET)
    assert report["needs_rebalance"]
    plan = plan_trades(state, TARGET)
    sides = {o["symbol"]: o["side"] for o in plan.orders}
    assert sides.get("OLD.IS") == "SELL" and sides.get("THYAO.IS") == "SELL"
    after_q = dict(q)
    for o in plan.orders:
        after_q[o["symbol"]] = after_q.get(o["symbol"], 0.0) + (
            o["quantity"] if o["side"] == "BUY" else -o["quantity"]
        )
    after = PortfolioState(cash=plan.cash_after, quantities=after_q, prices=PRICES)
    outside = [r for r in drift_report(after, TARGET)["assets"] if r["outside_band"]]
    assert outside == []


def test_min_turnover_beats_exact_target() -> None:
    q = _at_target()
    q["THYAO.IS"] *= 2.0
    state = PortfolioState(cash=0.0, quantities=q, prices=PRICES)
    plan = plan_trades(state, TARGET)
    exact = sum(abs(TARGET.get(s, 0) * state.total - v) for s, v in state.values.items())
    assert sum(o["amount"] for o in plan.orders) < exact


# ------------------------------------------------------------------ costs & taxes


def test_cost_model_commission_bsmv_spread() -> None:
    c = CostModel().estimate("THYAO.IS", 100_000)
    policy = get_policy()
    assert c.commission == pytest.approx(100_000 * policy.commission_bps("bist_hisse") / 1e4)
    assert c.bsmv == pytest.approx(c.commission * policy.costs["bsmv_rate"])
    assert c.spread == pytest.approx(100_000 * policy.spread_bps("bist_hisse") / 2e4)
    assert CostModel().estimate("TL_PPF", 10_000).total == 0.0


def _lot(sym: str, qty: float, cost: float, days_ago: int, lot_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=lot_id,
        symbol=sym,
        quantity_open=qty,
        unit_cost=cost,
        acquired_at=datetime.utcnow() - timedelta(days=days_ago),
    )


def test_hifo_realises_less_gain_than_fifo() -> None:
    lots = [_lot("TL_PPF", 10, 100.0, 400, 1), _lot("TL_PPF", 10, 180.0, 10, 2)]
    tax = TaxModel()
    fifo = tax.estimate_sale("TL_PPF", 10, 200.0, lots, method="FIFO")
    hifo = tax.estimate_sale("TL_PPF", 10, 200.0, lots, method="HIFO")
    assert hifo.realized_gain < fifo.realized_gain
    rate = get_policy().withholding_rate("para_piyasasi", 10)
    assert hifo.tax == pytest.approx(10 * 20.0 * rate)


def test_losses_offset_gains_and_harvest_candidates() -> None:
    lots = [_lot("ALTIN_TL", 5, 300.0, 50, 1), _lot("ALTIN_TL", 5, 100.0, 50, 2)]
    est = TaxModel().estimate_sale("ALTIN_TL", 10, 200.0, lots)
    assert est.realized_gain == pytest.approx(0.0) and est.tax >= 0
    cands = harvest_candidates(lots, {"ALTIN_TL": 200.0})
    assert len(cands) == 1 and cands[0]["unrealized_loss"] == pytest.approx(-500.0)
    assert cands[0]["potential_tax_offset"] == pytest.approx(
        500 * get_policy().withholding_rate("altin", 50)
    )


# ------------------------------------------------------------------ API / broker


def _executed(client: TestClient) -> tuple[int, dict]:
    cid = create_customer(client)["id"]
    pid = create_portfolio(client, cid, cash=500_000.0, holdings={})["id"]
    prop = client.post(f"/api/v1/advisor/rebalance/{pid}", params={"customer_id": cid}).json()
    done = client.post(f"/api/v1/proposals/{prop['proposal_id']}/approve").json()
    return pid, done


def test_broker_applies_slippage_fees_and_lots(client: TestClient) -> None:
    pid, done = _executed(client)
    slip = get_policy().costs["slippage_bps"] / 1e4
    for fill in done["execution_report"]["fills"]:
        ref = done["prices"][fill["symbol"]] if fill["symbol"] in done["prices"] else None
        if ref:
            assert fill["price"] == pytest.approx(ref * (1 + slip), rel=1e-6)
    ledger = client.get("/api/v1/transactions", params={"portfolio_id": pid}).json()
    assert sum(t["fees"] for t in ledger) == pytest.approx(
        done["execution_report"]["total_fees"], abs=0.05
    )
    valuation = client.get(f"/api/v1/portfolios/{pid}/valuation").json()
    assert all(item["avg_cost"] is not None for item in valuation["items"])
    assert client.get(f"/api/v1/portfolios/{pid}/tax-harvest").json()["portfolio_id"] == pid


def test_expired_proposal_cannot_be_approved(client: TestClient) -> None:
    from core.database import session_factory
    from models import RebalanceProposal
    from models.base import utcnow

    cid = create_customer(client)["id"]
    pid = create_portfolio(client, cid)["id"]
    prop = client.post(f"/api/v1/advisor/rebalance/{pid}", params={"customer_id": cid}).json()

    async def _age() -> None:
        async with session_factory() as s:
            await s.execute(
                update(RebalanceProposal).values(expires_at=utcnow() - timedelta(minutes=1))
            )
            await s.commit()

    client.portal.call(_age)
    resp = client.post(f"/api/v1/proposals/{prop['proposal_id']}/approve")
    assert resp.status_code == 409
    assert client.get(f"/api/v1/proposals/{prop['proposal_id']}").json()["status"] == "SURESI_DOLDU"


def test_price_move_expires_proposal(client: TestClient) -> None:
    from core.database import session_factory
    from models import RebalanceProposal

    cid = create_customer(client)["id"]
    pid = create_portfolio(client, cid)["id"]
    prop = client.post(f"/api/v1/advisor/rebalance/{pid}", params={"customer_id": cid}).json()

    async def _shift() -> None:
        async with session_factory() as s:
            row = await s.get(RebalanceProposal, prop["proposal_id"])
            row.prices = {k: v * 1.5 for k, v in row.prices.items()}
            await s.commit()

    client.portal.call(_shift)
    resp = client.post(f"/api/v1/proposals/{prop['proposal_id']}/approve")
    assert resp.status_code == 409 and "moved" in resp.json()["detail"]


def test_scheduler_jobs(client: TestClient) -> None:
    from core.database import session_factory
    from core.scheduler import build_scheduler, drift_check_job, expire_job
    from models import RebalanceProposal
    from models.base import utcnow

    container = client.app.state.container  # type: ignore[attr-defined]
    cid = create_customer(client)["id"]
    pid = create_portfolio(client, cid)["id"]
    created = client.portal.call(drift_check_job, container)
    assert created and client.get(f"/api/v1/proposals/{created[0]}").json()["source"] == "scheduler"
    assert client.portal.call(drift_check_job, container) == []  # açık öneri varken tekrar yok

    async def _age() -> None:
        async with session_factory() as s:
            await s.execute(
                update(RebalanceProposal).values(expires_at=utcnow() - timedelta(minutes=1))
            )
            await s.commit()

    client.portal.call(_age)
    assert client.portal.call(expire_job, container) >= 1
    sched = build_scheduler(container)
    assert {j.id for j in sched.get_jobs()} >= {
        "drift_check",
        "calendar_check",
        "expire_proposals",
        "persist_prices",
    }
    assert pid
