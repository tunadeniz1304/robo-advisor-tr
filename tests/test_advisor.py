"""Integration tests: the full Robo-Advisor rebalancing workflow.

These tests exercise the entire LangGraph pipeline end-to-end through the API
against the real app wiring — with *offline* deterministic doubles for the
market source and the LLM client (dependency-injected, no network access):

    POST /api/v1/advisor/rebalance/{portfolio_id}?customer_id=…

Piyasa Ajanı (FakeMarketSource) -> Risk Ajanı (real RiskService, DB profile)
-> Portföy Yöneticisi (real Markowitz MPT + DeterministicLLM + DB UPDATE/INSERT).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.config import Settings
from models import Customer
from routers import advisor as advisor_router
from services.advisor_service import AdvisorService
from services.market_service import MarketService
from services.portfolio_service import PortfolioService
from services.risk_service import RiskService
from tests.conftest import (
    DeterministicLLM,
    FakeMarketSource,
    create_customer,
    create_portfolio,
)


@pytest.fixture()
def advisor_client(settings: Settings) -> TestClient:
    """TestClient with the advisor dependency overridden with offline doubles."""
    from core.app import create_app

    app: FastAPI = create_app(settings=settings)

    def _override() -> AdvisorService:
        return AdvisorService(
            settings=settings,
            market_service=MarketService(FakeMarketSource(symbols=[])),
            risk_service=RiskService(),
            portfolio_service=PortfolioService(),
            llm_client=DeterministicLLM(),
        )

    app.dependency_overrides[advisor_router.get_advisor_service] = _override
    # Context manager'ı dışarıdan girmek zorundayız: lifespan (adopt_engine)
    # yalnızca ``__enter__`` ile tetiklenir.
    with TestClient(app) as c:
        yield c


def test_advisor_rebalance_returns_weights_and_orders(advisor_client: TestClient) -> None:
    """A full run returns weights, orders, a report and no error."""
    customer_id = create_customer(advisor_client)["id"]
    portfolio_id = create_portfolio(advisor_client, customer_id)["id"]

    resp = advisor_client.post(
        f"/api/v1/advisor/rebalance/{portfolio_id}", params={"customer_id": customer_id}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["portfolio_id"] == portfolio_id
    assert body["error"] is None
    assert body["weights"], "weights should be non-empty"
    assert body["report"]  # DeterministicLLM narrative present

    # Weights are non-negative; risky weights sum to the risk agency's equity
    # ceiling (the remainder is cash), never above the ceiling.
    assert all(w >= 0 for w in body["weights"].values())
    total = sum(body["weights"].values())
    assert 0.0 < total <= 1.0

    # The ceiling equals RiskService category_bounds for this profile
    # (declared_tolerance=4, horizon=10y, income=45k -> Balanced -> 0.50).
    ceiling = (
        RiskService()
        .assess(
            Customer(
                full_name="x",
                email="x@y.z",
                investment_horizon_years=10,
                monthly_income=45000.0,
                declared_risk_tolerance=4,
            )
        )
        .max_equity_weight
    )
    assert abs(total - ceiling) < 1e-6


def test_advisor_rebalance_persists_transactions(advisor_client: TestClient) -> None:
    """The rebalancing writes SQL INSERT rows into the transactions table."""
    customer_id = create_customer(advisor_client)["id"]
    portfolio_id = create_portfolio(advisor_client, customer_id)["id"]

    resp = advisor_client.post(
        f"/api/v1/advisor/rebalance/{portfolio_id}", params={"customer_id": customer_id}
    )
    assert resp.status_code == 200, resp.text
    orders = resp.json()["orders"]

    listed = advisor_client.get(
        "/api/v1/transactions", params={"portfolio_id": portfolio_id}
    ).json()
    assert len(listed) == len(orders)
    for row, order in zip(listed, orders, strict=True):
        assert row["ticker"] == order["ticker"]
        assert row["side"] == order["side"]
        assert row["reason"] == "rebalance"


def test_advisor_rebalance_updates_holdings_and_cash(advisor_client: TestClient) -> None:
    """The rebalancing writes SQL UPDATE to the portfolio holdings/cash."""
    customer_id = create_customer(advisor_client)["id"]
    portfolio_id = create_portfolio(advisor_client, customer_id)["id"]

    resp = advisor_client.post(
        f"/api/v1/advisor/rebalance/{portfolio_id}", params={"customer_id": customer_id}
    )
    assert resp.status_code == 200, resp.text

    updated = advisor_client.get(f"/api/v1/portfolios/{portfolio_id}").json()
    # Holdings now reflect target quantities.
    assert set(updated["holdings"].keys()) == set(resp.json()["weights"].keys())
    assert updated["cash"] >= 0


def test_advisor_rebalance_unknown_portfolio(advisor_client: TestClient) -> None:
    """An unknown portfolio id yields a clean 4xx, not a 500."""
    resp = advisor_client.post("/api/v1/advisor/rebalance/9999", params={"customer_id": 1})
    assert resp.status_code == 422


def test_llm_missing_returns_503(client: TestClient) -> None:
    """Without LLM credentials the advisor endpoint fails gracefully (503)."""
    customer_id = create_customer(client)["id"]
    portfolio_id = create_portfolio(client, customer_id)["id"]

    resp = client.post(
        f"/api/v1/advisor/rebalance/{portfolio_id}", params={"customer_id": customer_id}
    )
    assert resp.status_code == 503


def test_advisor_persists_audit_run(advisor_client: TestClient) -> None:
    """A successful run writes an immutable AdvisorRun row (audit trail)."""
    customer_id = create_customer(advisor_client)["id"]
    portfolio_id = create_portfolio(advisor_client, customer_id)["id"]

    resp = advisor_client.post(
        f"/api/v1/advisor/rebalance/{portfolio_id}", params={"customer_id": customer_id}
    )
    assert resp.status_code == 200, resp.text

    runs = advisor_client.get("/api/v1/runs", params={"portfolio_id": portfolio_id}).json()
    assert len(runs) == 1
    run = runs[0]

    assert run["portfolio_id"] == portfolio_id
    assert run["customer_id"] == customer_id
    assert run["status"] == "success"
    assert run["error"] is None
    assert run["report"]  # DeterministicLLM narrative recorded
    assert run["orders"]  # executed orders snapshotted
    assert set(run["target_weights"].keys()) == set(resp.json()["weights"].keys())
    assert run["created_at"] is not None

    # Single-run detail endpoint resolves the same row.
    detail = advisor_client.get(f"/api/v1/runs/{run['id']}").json()
    assert detail["id"] == run["id"]
    assert detail["status"] == "success"
