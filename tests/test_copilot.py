"""F7: copilot tool-use loop (fake LLMs), permission envelope, SSE and
grounded commentary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from core.app import create_app
from llm.clients import LLMClient, LLMProviderError, LLMResponse, ToolCall
from tests.conftest import (
    FakeMarketSource,
    auth_headers,
    create_customer,
    create_portfolio,
    login,
    make_settings,
)


class ScriptedLLM(LLMClient):
    """Live-mode fake that follows a script of responses."""

    mode = "live"
    model = "scripted"

    def __init__(self, script: list[LLMResponse | Exception]) -> None:
        self.script = list(script)
        self.seen: list[list[dict[str, Any]]] = []

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        self.seen.append(messages)
        item = self.script.pop(0) if self.script else LLMResponse(content='{"yanit": "Tamam."}')
        if isinstance(item, Exception):
            raise item
        return item


def _app(tmp_path: Path, llm: LLMClient) -> TestClient:
    return TestClient(
        create_app(make_settings(tmp_path), market_source=FakeMarketSource(), llm_client=llm)
    )


def _setup(c: TestClient) -> tuple[int, int]:
    c.headers.update(auth_headers(login(c)))
    cid = create_customer(c)["id"]
    pid = create_portfolio(c, cid)["id"]
    return cid, pid


def test_demo_copilot_answers_goal_and_fx_questions(client: TestClient) -> None:
    cid = create_customer(client)["id"]
    create_portfolio(client, cid)
    client.post(
        f"/api/v1/customers/{cid}/goals",
        json={
            "goal_type": "emeklilik",
            "name": "Emeklilik",
            "target_amount_real": 3e6,
            "horizon_years": 20,
            "monthly_contribution": 10000,
        },
    )
    r = client.post(
        "/api/v1/copilot/ask", json={"message": "Emekliliğime yetişir miyim?", "customer_id": cid}
    ).json()
    assert r["tools"] == ["simulate_goal"] and "başarı olasılığı" in r["answer"]
    assert "yatırım tavsiyesi değildir" in r["answer"] and r["llm_mode"] == "demo"
    fx = client.post(
        "/api/v1/copilot/ask", json={"message": "Dolar %30 artarsa ne olur?", "customer_id": cid}
    ).json()
    assert fx["tools"] == ["run_stress"] and "USDTRY" in fx["answer"]
    audit = client.get("/api/v1/audit", params={"action": "copilot.tool"}).json()
    assert len(audit) == 2


def test_live_tool_loop_and_no_execution_tool(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            LLMResponse(
                content="", tool_calls=[ToolCall(id="1", name="get_portfolio", arguments={})]
            ),
            LLMResponse(
                content="",
                tool_calls=[ToolCall(id="2", name="execute_orders", arguments={"all": True})],
            ),
            LLMResponse(content='{"yanit": "Portföyünüz incelendi; emir yürütme yetkim yok."}'),
        ]
    )
    with _app(tmp_path, llm) as c:
        cid, pid = _setup(c)
        r = c.post(
            "/api/v1/copilot/ask", json={"message": "Portföyümü sat", "customer_id": cid}
        ).json()
        assert r["tools"] == ["get_portfolio", "execute_orders"] and r["llm_mode"] == "live"
        tool_msgs = [m for m in llm.seen[-1] if m.get("role") == "tool"]
        assert "yetki zarfının dışında" in tool_msgs[1]["content"]
        assert c.get("/api/v1/transactions", params={"portfolio_id": pid}).json() == []
        tools = {
            t["function"]["name"]
            for t in __import__("agents.copilot.tools", fromlist=["x"]).TOOL_SPECS
        }
        assert not any("execute" in t or "approve" in t for t in tools)


def test_preview_rebalance_only_creates_pending_proposal(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            LLMResponse(
                content="", tool_calls=[ToolCall(id="1", name="preview_rebalance", arguments={})]
            ),
            LLMResponse(content='{"yanit": "Taslak öneri hazır, onayınızı bekliyor."}'),
        ]
    )
    with _app(tmp_path, llm) as c:
        cid, pid = _setup(c)
        c.post("/api/v1/copilot/ask", json={"message": "dengele", "customer_id": cid})
        props = c.get("/api/v1/proposals", params={"portfolio_id": pid}).json()
        assert (
            len(props) == 1
            and props[0]["status"] == "ONAY_BEKLIYOR"
            and props[0]["source"] == "copilot"
        )


def test_step_limit_is_enforced(tmp_path: Path) -> None:
    loop = [
        LLMResponse(
            content="", tool_calls=[ToolCall(id=str(i), name="get_risk_profile", arguments={})]
        )
        for i in range(10)
    ]
    with _app(tmp_path, ScriptedLLM(loop)) as c:
        cid, _ = _setup(c)
        r = c.post("/api/v1/copilot/ask", json={"message": "risk", "customer_id": cid}).json()
        assert len(r["steps"]) == 6 and "adım sınırına" in r["answer"]


def test_hallucinated_numbers_fall_back_to_grounded_answer(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            LLMResponse(
                content="", tool_calls=[ToolCall(id="1", name="get_portfolio", arguments={})]
            ),
            LLMResponse(
                content='{"yanit": "Portföyünüz yılda %87 kazandıracak, 9.999.999 TL olacak."}'
            ),
        ]
    )
    with _app(tmp_path, llm) as c:
        cid, _ = _setup(c)
        r = c.post("/api/v1/copilot/ask", json={"message": "ne olacak", "customer_id": cid}).json()
        assert r["llm_mode"] == "fallback" and r["llm_error_kind"] == "number_guard"
        assert "%87" not in r["answer"] and "Portföy değeri" in r["answer"]


def test_react_fallback_when_tools_unsupported(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            LLMProviderError("no tools", kind="tools_unsupported"),
            LLMResponse(content='{"arac": "get_risk_profile", "argumanlar": {}}'),
            LLMResponse(content='{"yanit": "Risk profiliniz incelendi."}'),
        ]
    )
    with _app(tmp_path, llm) as c:
        cid, _ = _setup(c)
        r = c.post("/api/v1/copilot/ask", json={"message": "riskim ne", "customer_id": cid}).json()
        assert r["tools"] == ["get_risk_profile"] and "Risk profiliniz" in r["answer"]


def test_sse_stream_and_permissions(client: TestClient) -> None:
    cid = create_customer(client)["id"]
    create_portfolio(client, cid)
    with client.stream(
        "POST", "/api/v1/copilot/chat", json={"message": "Neden bu dağılım?", "customer_id": cid}
    ) as resp:
        text = "".join(resp.iter_text())
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert "event: step" in text and "event: token" in text and "event: done" in text
    done = json.loads(text.split("event: done\ndata: ")[1].split("\n")[0])
    assert done["llm_mode"] == "demo"
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "username": "cop",
            "password": "GucluSifre!1",
            "full_name": "C P",
            "email": "c@p.com",
        },
    ).json()
    h = auth_headers(reg["access_token"])
    assert (
        client.post(
            "/api/v1/copilot/ask", headers=h, json={"message": "x portföy", "customer_id": cid}
        ).status_code
        == 404  # v2: yabancı kaynak = 404 (kimlik tahmini yok)
    )


def test_commentary_and_letter_are_grounded(client: TestClient) -> None:
    from llm.guard import check_numbers

    m = client.post("/api/v1/commentary/market").json()
    check_numbers(" ".join([m["ozet"], *m["maddeler"]]), m["data"])
    assert m["llm_mode"] == "demo"
    cid = create_customer(client)["id"]
    pid = create_portfolio(client, cid)["id"]
    letter = client.get(f"/api/v1/portfolios/{pid}/letter").json()
    assert letter["baslik"] and letter["performans"]
    page = client.get(f"/api/v1/portfolios/{pid}/letter", params={"format": "html"})
    assert (
        page.headers["content-type"].startswith("text/html")
        and "yatırım tavsiyesi değildir" in page.text
    )
