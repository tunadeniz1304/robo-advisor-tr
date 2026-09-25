"""Copilot (SSE streaming) and Aladdin-style auto commentary endpoints."""

from __future__ import annotations

import html
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from agents.copilot.agent import CopilotAgent
from agents.copilot.tools import CopilotContext
from core.deps import (
    CurrentUser,
    SessionDep,
    UserDep,
    load_customer_checked,
    load_portfolio_checked,
)
from llm.prompts import DISCLAIMER
from services.analytics.history import portfolio_report
from services.regime import cached_regime

router = APIRouter(tags=["copilot"])


class CopilotIn(BaseModel):
    message: str = Field(min_length=2, max_length=1000)
    customer_id: int | None = None
    portfolio_id: int | None = None


async def _context(
    request: Request, body: CopilotIn, session: SessionDep, user: CurrentUser
) -> CopilotContext:
    customer_id = body.customer_id or user.customer_id
    if customer_id is None:
        raise HTTPException(status_code=422, detail="customer_id gerekli.")
    await load_customer_checked(session, user, customer_id)
    if body.portfolio_id is not None:
        p = await load_portfolio_checked(session, user, body.portfolio_id)
        if p.customer_id != customer_id:
            raise HTTPException(
                status_code=404, detail=f"Portfolio {body.portfolio_id} bulunamadı."
            )
    return CopilotContext(
        request.app.state.container, customer_id, body.portfolio_id, user.id, user.role
    )


@router.post("/copilot/ask", summary="Copilot (tek yanıt)")
async def ask(
    request: Request, body: CopilotIn, session: SessionDep, user: UserDep
) -> dict[str, Any]:
    ctx = await _context(request, body, session, user)
    steps: list[dict[str, Any]] = []
    final: dict[str, Any] = {}
    async for event in CopilotAgent(ctx.container.gateway).run(body.message, ctx):
        if event["event"] == "step":
            steps.append(event)
        else:
            final = event
    return {**final, "steps": steps}


@router.post("/copilot/chat", summary="Copilot (SSE akışı)")
async def chat(
    request: Request, body: CopilotIn, session: SessionDep, user: UserDep
) -> StreamingResponse:
    ctx = await _context(request, body, session, user)

    async def stream() -> AsyncIterator[str]:
        async for event in CopilotAgent(ctx.container.gateway).run(body.message, ctx):
            if event["event"] == "answer":
                words = event["answer"].split(" ")
                for i in range(0, len(words), 6):
                    yield f"event: token\ndata: {json.dumps(' '.join(words[i : i + 6]) + ' ', ensure_ascii=False)}\n\n"
                meta = {k: event[k] for k in ("tools", "llm_mode", "llm_error_kind")}
                yield f"event: done\ndata: {json.dumps(meta, ensure_ascii=False)}\n\n"
            else:
                yield f"event: step\ndata: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@router.post("/commentary/market", summary="Otomatik piyasa yorumu (veriyle topraklanmış)")
async def market_commentary(request: Request, user: UserDep) -> dict[str, Any]:
    c = request.app.state.container
    symbols = ["XU100.IS", "XU030.IS", "ALTIN_TL", "USDTRY", "EUROBOND_TL", "TL_TAHVIL"]
    rets = await c.market.returns(symbols, freq="M")
    daily = await c.market.returns(symbols)
    reg = await cached_regime(c)
    ctx = {
        "piyasa": {
            s: {
                "getiri_1_ay": round(float(rets[s].iloc[-1]), 4),
                "volatilite": round(float(daily[s].tail(63).std() * 252**0.5), 4),
            }
            for s in symbols
            if s in rets
        },
        "makro": {
            "politika_faizi": round(c.market.policy_rate(), 4),
            "tufe_yillik": round(c.market.inflation_yoy(), 4),
        },
        "rejim": {"etiket": reg.get("label")},
    }
    gen = await c.gateway.generate("market_commentary", ctx)
    return {**gen.data.model_dump(), "llm_mode": gen.mode, "data": ctx, "disclaimer": DISCLAIMER}


@router.get(
    "/portfolios/{portfolio_id}/letter", summary="Haftalık portföy mektubu (JSON veya HTML)"
)
async def letter(
    request: Request, portfolio_id: int, session: SessionDep, user: UserDep, format: str = "json"
) -> Any:  # noqa: A002
    portfolio = await load_portfolio_checked(session, user, portfolio_id)
    c = request.app.state.container
    rep = await portfolio_report(session, c.market, portfolio)
    sheet = rep["tear_sheet"]
    rets = await c.market.returns(["XU100.IS", "ALTIN_TL", "USDTRY"], freq="M")
    ctx = {
        "portfoy": {
            "toplam_deger": rep["value_curve"][-1]["value"]
            if rep["value_curve"]
            else float(portfolio.cash)
        },
        "performans": {
            "toplam_getiri": round(rep["twr_cumulative"], 4),
            "volatilite": round(float(sheet.get("volatility", 0.0)), 4),
        },
        "risk": {
            "maks_dusus": round(float(sheet.get("max_drawdown", 0.0)), 4),
            "var_95": round(float(sheet.get("var_95_hist", 0.0)), 4),
        },
        "piyasa": {
            s: {"getiri_1_ay": round(float(rets[s].iloc[-1]), 4), "volatilite": 0.0}
            for s in rets.columns
        },
        "makro": {
            "politika_faizi": round(c.market.policy_rate(), 4),
            "tufe_yillik": round(c.market.inflation_yoy(), 4),
        },
    }
    gen = await c.gateway.generate("portfolio_letter", ctx)
    data = gen.data.model_dump()
    if format == "html":
        body = "".join(
            f"<h2>{html.escape(k.capitalize())}</h2><p>{html.escape(str(v))}</p>"
            for k, v in data.items()
            if k != "baslik"
        )
        page = (
            f"<!doctype html><html lang='tr'><meta charset='utf-8'><title>{html.escape(data['baslik'])}</title>"
            f"<body style='font-family:sans-serif;max-width:720px;margin:auto'><h1>{html.escape(data['baslik'])}</h1>"
            f"{body}<hr><small>{html.escape(DISCLAIMER)}</small></body></html>"
        )
        return HTMLResponse(page)
    return {**data, "llm_mode": gen.mode, "disclaimer": DISCLAIMER}
