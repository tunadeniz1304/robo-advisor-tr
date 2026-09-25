"""Çalışan bir sunucuya karşı uçtan uca duman testi (Docker/prod doğrulaması).

Kullanım::

    python scripts/e2e_smoke.py --base http://localhost:8000

Akış: sağlık → demo kullanıcı girişi → uygunluk anketi → hedef + simülasyon
→ öneri → onay → yürütme → stres → copilot → arayüz. Exit 0/1.
"""

from __future__ import annotations

import argparse
import sys

import httpx

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # TextIO lacks it; real streams have it
    except AttributeError:  # pragma: no cover
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--user", default="demo.genc")
    ap.add_argument("--password", default="Demo!2345")
    args = ap.parse_args()
    c = httpx.Client(base_url=args.base, timeout=120)

    def step(name: str, resp: httpx.Response, ok: tuple[int, ...] = (200, 201)) -> dict:
        mark = "OK " if resp.status_code in ok else "HATA"
        print(f"[{mark}] {name}: {resp.status_code}")
        if resp.status_code not in ok:
            print(resp.text[:500])
            raise SystemExit(1)
        return resp.json() if "json" in resp.headers.get("content-type", "") else {}

    step("health/ready", c.get("/health/ready"))
    llm = step("llm/status", c.get("/api/v1/llm/status"))
    print(f"      AI modu: {llm['mode']} ({llm['model']})")
    tok = step(
        "login",
        c.post("/api/v1/auth/login", json={"username": args.user, "password": args.password}),
    )
    c.headers["Authorization"] = f"Bearer {tok['access_token']}"
    cid = tok["customer_id"]
    q = step("anket", c.get("/api/v1/suitability/questionnaire"))
    answers = {x["id"]: x["options"][len(x["options"]) // 2]["value"] for x in q["questions"]}
    prof = step(
        "risk profili",
        c.post(
            f"/api/v1/customers/{cid}/risk-profile",
            json={"answers": answers, "confirm_inconsistencies": True},
        ),
    )
    print(f"      seviye: {prof['profile']['risk_level']}/10")
    goal = step(
        "hedef",
        c.post(
            f"/api/v1/customers/{cid}/goals",
            json={
                "goal_type": "ev",
                "name": "Smoke hedef",
                "target_amount_real": 1500000,
                "horizon_years": 6,
                "monthly_contribution": 10000,
            },
        ),
    )
    sim = step("simülasyon", c.post(f"/api/v1/goals/{goal['id']}/simulate"))
    print(f"      başarı olasılığı: {sim['result']['success_probability']:.2%}")
    pid = step("portföy", c.get("/api/v1/portfolios"))[0]["id"]
    prop = step("öneri", c.post(f"/api/v1/advisor/rebalance/{pid}", params={"customer_id": cid}))
    if prop.get("proposal_id"):
        done = step("onay+yürütme", c.post(f"/api/v1/proposals/{prop['proposal_id']}/approve"))
        print(
            f"      durum: {done['status']}, işlem: {len(done['execution_report'].get('fills', []))}"
        )
    else:
        print(f"      {prop.get('message')}")
    step("stres", c.post(f"/api/v1/portfolios/{pid}/stress", json={"scenario": "bist_down_25"}))
    ans = step(
        "copilot", c.post("/api/v1/copilot/ask", json={"message": "Emekliliğime yetişir miyim?"})
    )
    print(f"      copilot modu: {ans['llm_mode']}")
    page = c.get("/")
    print(f"[{'OK ' if page.status_code == 200 else 'HATA'}] arayüz: {page.status_code}")
    return 0 if page.status_code == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
