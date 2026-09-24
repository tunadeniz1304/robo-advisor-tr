"""Integration tests: Customer entity CRUD through the live API.

These tests exercise the async SQLAlchemy layer end-to-end through the
FastAPI TestClient against a file-backed SQLite database, including the
unique-email constraint and cascading deletes.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import create_customer


def _emails(client: TestClient) -> list[str]:
    resp = client.get("/api/v1/customers")
    assert resp.status_code == 200
    return [c["email"] for c in resp.json()]


def test_create_list_get_customer(client: TestClient) -> None:
    """A created customer appears in list and is fetchable by id."""
    created = create_customer(client)
    customer_id = created["id"]

    assert created["full_name"] == "Ayşe Yılmaz"
    assert created["investment_horizon_years"] == 10
    assert created["declared_risk_tolerance"] == 4

    # List
    resp = client.get("/api/v1/customers")
    assert resp.status_code == 200
    assert customer_id in {c["id"] for c in resp.json()}

    # Get by id
    resp = client.get(f"/api/v1/customers/{customer_id}")
    assert resp.status_code == 200
    assert resp.json()["email"] == "ayse.test@example.com"


def test_customer_email_uniqueness_enforced(client: TestClient) -> None:
    """The DB unique index translates to a 409 on duplicate email."""
    create_customer(client, email="dupe@example.com")

    resp = client.post(
        "/api/v1/customers",
        json={
            "full_name": "Başka Kişi",
            "email": "dupe@example.com",
            "investment_horizon_years": 5,
        },
    )
    assert resp.status_code == 409


def test_update_customer_partial(client: TestClient) -> None:
    """PATCH-style PUT updates only provided fields."""
    customer_id = create_customer(client)["id"]

    resp = client.put(f"/api/v1/customers/{customer_id}", json={"declared_risk_tolerance": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert body["declared_risk_tolerance"] == 2
    # Non-provided fields are preserved.
    assert body["full_name"] == "Ayşe Yılmaz"


def test_delete_customer_cascades_portfolios(client: TestClient) -> None:
    """Deleting a customer removes its portfolios (ON DELETE CASCADE)."""
    customer_id = create_customer(client)["id"]
    from tests.conftest import create_portfolio

    portfolio = create_portfolio(client, customer_id)

    resp = client.delete(f"/api/v1/customers/{customer_id}")
    assert resp.status_code == 204

    resp = client.get(f"/api/v1/portfolios/{portfolio['id']}")
    assert resp.status_code == 404


def test_customer_validation_min_tolerance(client: TestClient) -> None:
    """declared_risk_tolerance must lie in [1, 5]."""
    resp = client.post(
        "/api/v1/customers",
        json={"full_name": "Z", "email": "z@example.com", "declared_risk_tolerance": 0},
    )
    assert resp.status_code == 422
