"""Integration tests: portfolio + transaction CRUD through the live API."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import create_customer, create_portfolio


def test_portfolio_lifecycle(client: TestClient) -> None:
    """Create, list, update and fetch a portfolio for a customer."""
    customer_id = create_customer(client)["id"]
    portfolio = create_portfolio(client, customer_id)

    assert portfolio["currency"] == "TRY"
    assert portfolio["cash"] == 250_000.0

    # List filtered by customer
    resp = client.get("/api/v1/portfolios", params={"customer_id": customer_id})
    assert resp.status_code == 200
    assert [p["id"] for p in resp.json()] == [portfolio["id"]]

    # Update name
    resp = client.put(f"/api/v1/portfolios/{portfolio['id']}", json={"name": "Yeni Portföy"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Yeni Portföy"


def test_create_transaction_manual(client: TestClient) -> None:
    """A manual transaction is recorded and listed under the portfolio."""
    customer_id = create_customer(client)["id"]
    portfolio_id = create_portfolio(client, customer_id)["id"]

    resp = client.post(
        "/api/v1/transactions",
        json={
            "portfolio_id": portfolio_id,
            "ticker": "THYAO.IS",
            "side": "BUY",
            "quantity": 10,
            "price": 320.0,
            "reason": "manual",
        },
    )
    assert resp.status_code == 201, resp.text
    tx = resp.json()
    assert tx["total_amount"] == 3200.0
    assert tx["side"] == "BUY"

    # Listed under the portfolio
    resp = client.get("/api/v1/transactions", params={"portfolio_id": portfolio_id})
    assert resp.status_code == 200
    assert len(resp.json()) == 1
