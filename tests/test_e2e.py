"""E2E: demo seed → login → onboarding → goal → proposal → approval →
execution, plus the SPA being served under a strict CSP."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from core.app import create_app
from services.demo_seed import DEMO_PW
from tests.conftest import auth_headers, login, make_settings
from tests.test_suitability import answers


def test_full_customer_journey_on_seeded_demo(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path, seed_demo=True))  # snapshot verisi, demo LLM
    with TestClient(app) as c:
        # Danışman kuyruğunda tohumlanmış bekleyen öneriler var
        staff = auth_headers(login(c, "danisman", "Danisman!2345"))
        queue = c.get("/api/v1/proposals", params={"status": "ONAY_BEKLIYOR"}, headers=staff).json()
        assert len(queue) >= 1 and len(c.get("/api/v1/customers", headers=staff).json()) == 3

        h = auth_headers(login(c, "demo.genc", DEMO_PW))
        me = c.get("/api/v1/auth/me", headers=h).json()
        cid = me["customer_id"]
        pid = c.get("/api/v1/portfolios", headers=h).json()[0]["id"]
        assert (
            len(
                c.get(
                    "/api/v1/transactions", params={"portfolio_id": pid, "limit": 500}, headers=h
                ).json()
            )
            > 20
        )
        report = c.get(f"/api/v1/portfolios/{pid}/report", headers=h).json()
        assert report["tear_sheet"]["observations"] > 200

        # Onboarding: anket → risk seviyesi
        prof = c.post(
            f"/api/v1/customers/{cid}/risk-profile", headers=h, json={"answers": answers()}
        )
        assert prof.status_code == 201 and 1 <= prof.json()["profile"]["risk_level"] <= 10
        # Hedef + simülasyon
        goal = c.post(
            f"/api/v1/customers/{cid}/goals",
            headers=h,
            json={
                "goal_type": "ev",
                "name": "Yazlık",
                "target_amount_real": 2e6,
                "horizon_years": 7,
                "monthly_contribution": 10000,
            },
        ).json()
        sim = c.post(f"/api/v1/goals/{goal['id']}/simulate", headers=h).json()
        assert 0 <= sim["result"]["success_probability"] <= 1
        # Öneri → önizleme → onay → yürütme
        prop = c.post(
            f"/api/v1/advisor/rebalance/{pid}", params={"customer_id": cid}, headers=h
        ).json()
        assert prop["status"] == "ONAY_BEKLIYOR" and prop["proposal"]["explanation"]
        done = c.post(f"/api/v1/proposals/{prop['proposal_id']}/approve", headers=h).json()
        assert done["status"] == "YURUTULDU" and done["execution_report"]["fills"]
        # Stres + copilot + denetim zinciri
        assert (
            c.post(
                f"/api/v1/portfolios/{pid}/stress", headers=h, json={"scenario": "usdtry_up_30"}
            ).status_code
            == 200
        )
        assert (
            "yatırım tavsiyesi"
            in c.post(
                "/api/v1/copilot/ask", headers=h, json={"message": "Neden bu dağılım?"}
            ).json()["answer"]
        )
        admin = auth_headers(login(c, "admin", "test-admin-pass"))
        assert c.get("/api/v1/audit/verify", headers=admin).json()["valid"] is True


def test_seed_is_idempotent(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, seed_demo=True)
    for _ in range(2):
        with TestClient(create_app(settings)) as c:
            count = len(c.get("/api/v1/customers", headers=auth_headers(login(c))).json())
    assert count == 3


def test_spa_is_served_with_csp(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200 and "Otonom Finansal Danışman" in page.text
    csp = page.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "unsafe-eval" not in csp
    assert "<script>" not in page.text and "onclick" not in page.text
    for asset in ("/assets/js/main.js", "/assets/app.css", "/assets/vendor/chart.umd.min.js"):
        assert client.get(asset).status_code == 200
