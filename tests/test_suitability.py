"""SPK suitability: scoring, caps, consistency, versioning and the gate."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from services.suitability.questionnaire import QUESTIONS, questionnaire_payload, validate_answers
from services.suitability.scoring import check_allocation, score_answers
from tests.conftest import auth_headers, create_customer


def answers(**overrides: str) -> dict[str, str]:
    """A balanced, consistent answer set (middle options) with overrides."""
    base = {
        q.id: sorted(q.options, key=lambda o: o.score)[len(q.options) // 2].value for q in QUESTIONS
    }
    base.update(overrides)
    return base


def extreme(high: bool) -> dict[str, str]:
    pick = max if high else min
    return {q.id: pick(q.options, key=lambda o: o.score).value for q in QUESTIONS}


# ------------------------------------------------------------------ scoring


def test_questionnaire_has_15_questions_in_5_sections() -> None:
    payload = questionnaire_payload()
    assert len(payload["questions"]) == 15
    assert {q["dimension"] for q in payload["questions"]} == {"knowledge", "capacity", "tolerance"}
    assert all("score" not in o for q in payload["questions"] for o in q["options"])


def test_extremes_map_to_levels_1_and_10() -> None:
    assert score_answers(extreme(True)).risk_level == 10
    assert score_answers(extreme(False)).risk_level == 1


def test_level_is_min_of_capacity_and_tolerance() -> None:
    a = extreme(True)
    a.update(
        {
            "dusus_tepkisi": "kismen_sat",
            "senaryo": "b",
            "oz_degerlendirme": "temkinli",
            "amac": "gelir",
        }
    )
    r = score_answers(a)
    assert r.capacity_level == 10 and r.tolerance_level < 5
    assert r.risk_level == r.tolerance_level


def test_short_horizon_high_tolerance_is_capped_by_capacity() -> None:
    a = extreme(True)
    a["vade"] = "v0"
    r = score_answers(a)
    assert r.risk_level <= 3
    codes = {w.code for w in r.warnings} | {c["code"] for c in r.caps}
    assert {"kisa_vade_yuksek_tolerans", "kisa_vade"} <= codes


@pytest.mark.parametrize(
    ("override", "limit", "code"),
    [
        ({"borc_orani": "d0"}, 3, "yuksek_borc"),
        ({"acil_durum_fonu": "yok"}, 5, "acil_fon_yok"),
        ({"bilgi": "yok", "deneyim": "yok", "islem_sikligi": "hic"}, 3, "bilgi_cok_dusuk"),
    ],
)
def test_capacity_and_knowledge_caps(override: dict[str, str], limit: int, code: str) -> None:
    a = extreme(True)
    a.update(override)
    r = score_answers(a)
    assert r.risk_level <= limit
    assert code in {c["code"] for c in r.caps}


def test_contradiction_is_blocking() -> None:
    r = score_answers(answers(dusus_tepkisi="hepsini_sat", senaryo="e"))
    assert r.needs_confirmation
    assert "kayip_toleransi_celiskisi" in {w.code for w in r.warnings}


def test_validation_reports_missing_and_invalid() -> None:
    res = validate_answers({"yas": "999", "bilinmeyen": "x"})
    assert not res.ok and "gelir" in res.missing and {"yas", "bilinmeyen"} <= set(res.invalid)


# ------------------------------------------------------------------ gate


def test_gate_blocks_risky_instruments_for_low_levels() -> None:
    gate = check_allocation(2, {"TL_PPF": 0.7, "THYAO.IS": 0.3})
    assert not gate.allowed
    assert gate.violations[0]["symbol"] == "THYAO.IS"
    assert "uygun değildir" in gate.violations[0]["message"]
    assert check_allocation(2, {"TL_PPF": 0.7, "ALTIN_TL": 0.3}).allowed


def test_gate_model_level_and_override() -> None:
    gate = check_allocation(4, {"TL_PPF": 1.0}, model_level=7)
    assert not gate.allowed and gate.violations[0]["type"] == "model_seviyesi"
    ok = check_allocation(4, {"TL_PPF": 1.0}, model_level=7, override_ack=True)
    assert ok.allowed and ok.overridden


# ------------------------------------------------------------------ API


def test_profile_api_flow_with_reask_confirmation_and_versions(client: TestClient) -> None:
    cid = create_customer(client)["id"]
    url = f"/api/v1/customers/{cid}/risk-profile"

    before = client.get(url).json()
    assert before["effective"]["source"] == "tahmini" and before["needs_profile"]

    bad = client.post(url, json={"answers": answers(dusus_tepkisi="hepsini_sat", senaryo="e")})
    assert bad.status_code == 409
    assert set(bad.json()["detail"]["reask"]) == {"dusus_tepkisi", "senaryo"}

    confirmed = client.post(
        url,
        json={
            "answers": answers(dusus_tepkisi="hepsini_sat", senaryo="e"),
            "confirm_inconsistencies": True,
        },
    )
    assert confirmed.status_code == 201
    assert confirmed.json()["profile"]["warnings"]

    second = client.post(url, json={"answers": answers()})
    assert second.status_code == 201 and second.json()["profile"]["version"] == 2

    state = client.get(url).json()
    assert state["effective"]["source"] == "profil" and not state["needs_profile"]
    assert [p["version"] for p in state["history"]] == [2, 1]
    assert [p["is_active"] for p in state["history"]] == [True, False]
    current = state["current"]
    valid_days = datetime.fromisoformat(current["valid_until"]) - datetime.fromisoformat(
        current["created_at"]
    )
    assert valid_days >= timedelta(days=364)

    audit = client.get("/api/v1/audit", params={"action": "suitability.profile"}).json()
    assert len(audit) == 2


def test_expired_profile_requires_renewal(client: TestClient) -> None:
    from core.database import session_factory
    from models import RiskProfile
    from models.base import utcnow

    cid = create_customer(client)["id"]
    client.post(f"/api/v1/customers/{cid}/risk-profile", json={"answers": answers()})

    async def _expire() -> None:
        async with session_factory() as s:
            await s.execute(update(RiskProfile).values(valid_until=utcnow() - timedelta(days=1)))
            await s.commit()

    client.portal.call(_expire)
    state = client.get(f"/api/v1/customers/{cid}/risk-profile").json()
    assert state["effective"]["expired"] and state["needs_profile"]


def test_customer_submits_own_profile_only(client: TestClient) -> None:
    other = create_customer(client, email="diger@example.com")["id"]
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "username": "anketci",
            "password": "GucluSifre!1",
            "full_name": "A K",
            "email": "a@k.com",
        },
    ).json()
    h = auth_headers(reg["access_token"])
    ok = client.post(
        f"/api/v1/customers/{reg['customer_id']}/risk-profile",
        headers=h,
        json={"answers": answers()},
    )
    assert ok.status_code == 201
    no = client.post(
        f"/api/v1/customers/{other}/risk-profile", headers=h, json={"answers": answers()}
    )
    assert no.status_code == 403
    assert client.get("/api/v1/suitability/questionnaire", headers=h).status_code == 200


def test_optimize_endpoint_respects_gate_and_audits_override(client: TestClient) -> None:
    cid = create_customer(client)["id"]
    low = extreme(False)
    low.update(
        {
            "bilgi": "fon",
            "deneyim": "fon",
            "vade": "v2",
            "acil_durum_fonu": "a2",
            "borc_orani": "d3",
        }
    )
    client.post(f"/api/v1/customers/{cid}/risk-profile", json={"answers": low})
    level = client.get(f"/api/v1/customers/{cid}/risk-profile").json()["effective"]["level"]
    assert level <= 2

    res = client.post(f"/api/v1/customers/{cid}/optimize", json={"method": "hrp"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert abs(sum(body["weights"].values()) - 1.0) < 1e-6
    assert not any(s.endswith(".IS") for s in body["weights"])  # hisse/endeks yok

    denied = client.post(f"/api/v1/customers/{cid}/optimize", json={"model_level": 8})
    assert denied.status_code == 403
    assert "uygun değildir" in denied.json()["detail"]["message"]

    forced = client.post(
        f"/api/v1/customers/{cid}/optimize", json={"model_level": 8, "override_ack": True}
    )
    assert forced.status_code == 200 and forced.json()["gate"]["overridden"]
    logs = client.get("/api/v1/audit", params={"action": "suitability.override"}).json()
    assert len(logs) == 1
