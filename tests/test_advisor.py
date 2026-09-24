"""E2E: proposal → approval → execution through the API and the LangGraph
workflow (offline market source, demo LLM).

Behaviour change vs v1: ``POST /advisor/rebalance`` no longer executes
orders; it returns a proposal awaiting approval. Execution happens exactly
once on ``POST /proposals/{id}/approve``.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from tests.conftest import auth_headers, create_customer, create_portfolio


def _propose(client: TestClient, **pf: object) -> tuple[int, int, dict]:
    cid = create_customer(client)["id"]
    pid = create_portfolio(client, cid, **pf)["id"]
    resp = client.post(f"/api/v1/advisor/rebalance/{pid}", params={"customer_id": cid})
    assert resp.status_code == 200, resp.text
    return cid, pid, resp.json()


def test_rebalance_creates_pending_proposal_without_executing(client: TestClient) -> None:
    _, pid, body = _propose(client)
    assert body["status"] == "ONAY_BEKLIYOR" and body["proposal_id"]
    assert body["orders"] and body["weights"]
    assert abs(sum(body["weights"].values()) - 1.0) < 1e-6
    assert "yatırım tavsiyesi değildir" in body["report"]
    prop = body["proposal"]
    assert prop["explanation"] and prop["risk_before"] and prop["risk_after"]
    assert prop["estimated_cost"] >= 0 and prop["expires_at"]
    # Onaysız hiçbir işlem yok
    assert client.get("/api/v1/transactions", params={"portfolio_id": pid}).json() == []


def test_approve_executes_once_and_updates_ledger(client: TestClient) -> None:
    _, pid, body = _propose(client)
    before = client.get(f"/api/v1/portfolios/{pid}").json()
    approved = client.post(
        f"/api/v1/proposals/{body['proposal_id']}/approve", headers={"Idempotency-Key": "k-1"}
    )
    assert approved.status_code == 200, approved.text
    data = approved.json()
    assert data["status"] == "YURUTULDU"
    fills = data["execution_report"]["fills"]
    ledger = client.get("/api/v1/transactions", params={"portfolio_id": pid}).json()
    assert len(ledger) == len(fills) > 0
    assert all(row["reason"] == "rebalance" for row in ledger)

    again = client.post(
        f"/api/v1/proposals/{body['proposal_id']}/approve", headers={"Idempotency-Key": "k-1"}
    )
    assert again.status_code == 200 and again.json()["status"] == "YURUTULDU"
    assert len(client.get("/api/v1/transactions", params={"portfolio_id": pid}).json()) == len(
        ledger
    )

    after = client.get(f"/api/v1/portfolios/{pid}").json()
    assert after["holdings"] != before["holdings"] and after["cash"] >= 0
    runs = client.get("/api/v1/runs", params={"portfolio_id": pid}).json()
    assert len(runs) == 1 and runs[0]["status"] == "success"
    actions = [
        a["action"]
        for a in client.get("/api/v1/audit", params={"customer_id": data["customer_id"]}).json()
    ]
    assert {"proposal.created", "proposal.approved", "proposal.executed"} <= set(actions)


def test_concurrent_double_approval_executes_once(client: TestClient) -> None:
    _, pid, body = _propose(client)
    url = f"/api/v1/proposals/{body['proposal_id']}/approve"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.post(url), range(2)))
    assert {r.status_code for r in results} == {200}
    fills = client.get(f"/api/v1/proposals/{body['proposal_id']}").json()["execution_report"][
        "fills"
    ]
    assert len(client.get("/api/v1/transactions", params={"portfolio_id": pid}).json()) == len(
        fills
    )


def test_within_band_portfolio_gets_no_orders(client: TestClient) -> None:
    cid, pid, body = _propose(client)
    client.post(f"/api/v1/proposals/{body['proposal_id']}/approve")
    again = client.post(f"/api/v1/advisor/rebalance/{pid}", params={"customer_id": cid}).json()
    assert (
        again["needs_rebalance"] is False and again["orders"] == [] and again["proposal_id"] is None
    )


def test_reject_closes_proposal(client: TestClient) -> None:
    _, pid, body = _propose(client)
    rej = client.post(
        f"/api/v1/proposals/{body['proposal_id']}/reject", json={"reason": "Beklemek istiyorum"}
    )
    assert rej.status_code == 200 and rej.json()["status"] == "REDDEDILDI"
    assert client.post(f"/api/v1/proposals/{body['proposal_id']}/approve").status_code == 409
    assert client.get("/api/v1/transactions", params={"portfolio_id": pid}).json() == []


def test_new_proposal_supersedes_pending_one(client: TestClient) -> None:
    cid, pid, first = _propose(client)
    second = client.post(f"/api/v1/advisor/rebalance/{pid}", params={"customer_id": cid}).json()
    assert second["proposal_id"] != first["proposal_id"]
    old = client.get(f"/api/v1/proposals/{first['proposal_id']}").json()
    assert old["status"] == "SURESI_DOLDU"
    assert client.post(f"/api/v1/proposals/{first['proposal_id']}/approve").status_code == 409


def test_idempotent_creation(client: TestClient) -> None:
    cid = create_customer(client)["id"]
    pid = create_portfolio(client, cid)["id"]
    h = {"Idempotency-Key": "olustur-1"}
    a = client.post(
        f"/api/v1/advisor/rebalance/{pid}", params={"customer_id": cid}, headers=h
    ).json()
    b = client.post(
        f"/api/v1/advisor/rebalance/{pid}", params={"customer_id": cid}, headers=h
    ).json()
    assert a["proposal_id"] == b["proposal_id"]


def test_customer_approves_own_proposal_only(client: TestClient) -> None:
    _, _, other = _propose(client)
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "username": "onayci",
            "password": "GucluSifre!1",
            "full_name": "O K",
            "email": "o@k.com",
            "initial_cash": 50000,
        },
    ).json()
    h = auth_headers(reg["access_token"])
    assert (
        client.post(f"/api/v1/proposals/{other['proposal_id']}/approve", headers=h).status_code
        == 403
    )
    pid = client.get("/api/v1/portfolios", headers=h).json()[0]["id"]
    own = client.post(
        f"/api/v1/advisor/rebalance/{pid}", params={"customer_id": reg["customer_id"]}, headers=h
    )
    assert own.status_code == 200 and own.json()["status"] == "ONAY_BEKLIYOR"
    done = client.post(f"/api/v1/proposals/{own.json()['proposal_id']}/approve", headers=h)
    assert done.status_code == 200 and done.json()["status"] == "YURUTULDU"
    listed = client.get("/api/v1/proposals", headers=h).json()
    assert [p["customer_id"] for p in listed] == [reg["customer_id"]]


def test_unknown_portfolio_is_422(client: TestClient) -> None:
    assert (
        client.post("/api/v1/advisor/rebalance/9999", params={"customer_id": 1}).status_code == 422
    )


def test_rebalance_without_key_runs_in_demo_mode(client: TestClient) -> None:
    _, _, body = _propose(client)
    assert body["llm_mode"] == "demo"
