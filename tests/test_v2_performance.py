"""v2 audit findings A.1–A.4: one ledger-based performance engine.

* A.1 — ``/performance`` and ``/report`` must agree (same engine, real TL
  risk-free rate everywhere).
* A.2 — GIPS flow classification: only client deposits/withdrawals are
  external; dividends, coupons, interest, fees, taxes and commissions are
  portfolio-internal and belong in the TWR.
* A.3 — ledger events on non-trading days roll forward to the next
  valuation day instead of being dropped.
* A.4 — explicit metric names (cumulative vs annualised) with the period.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tests.conftest import create_customer, create_portfolio

DAYS = pd.bdate_range("2026-08-03", "2026-08-14")  # Pzt 3 Ağu → Cum 14 Ağu (10 işlem günü)
PRICE = 100.0


class StubMarket:
    """Constant-price market so that only ledger events move the value."""

    def __init__(self, symbols: list[str], rf: float = 0.40) -> None:
        self.panel = pd.DataFrame({s: PRICE for s in symbols}, index=DAYS)
        self._rf = rf

    async def history(self, symbols: list[str]) -> pd.DataFrame:
        return self.panel[[s for s in symbols if s in self.panel.columns]]

    async def returns(self, symbols: list[str], **_: Any) -> pd.DataFrame:
        return (await self.history(symbols)).pct_change().iloc[1:]

    def risk_free_rate(self) -> float:
        return self._rf

    def inflation_yoy(self) -> float:
        return 0.30


def _portfolio_with_flows(
    client: TestClient, *, cash: float, flows: list[tuple[str, float, datetime]]
) -> int:
    """Create a 100×THYAO portfolio whose *current* cash already includes ``flows``."""
    cid = create_customer(client, email=f"perf{len(flows)}{cash}@example.com")["id"]
    pid = int(create_portfolio(client, cid, cash=cash, holdings={"THYAO.IS": 100.0})["id"])

    from core.database import session_factory
    from models import CashFlow

    async def _insert() -> None:
        async with session_factory() as s:
            for kind, amount, when in flows:
                s.add(
                    CashFlow(
                        portfolio_id=pid, kind=kind, amount=Decimal(str(amount)), occurred_at=when
                    )
                )
            await s.commit()

    client.portal.call(_insert)
    return pid


def _history(client: TestClient, pid: int) -> tuple[pd.Series, pd.Series]:
    from core.database import session_factory
    from models import Portfolio
    from services.analytics.history import value_history

    async def _run() -> tuple[pd.Series, pd.Series]:
        async with session_factory() as s:
            portfolio = await s.get(Portfolio, pid)
            return await value_history(s, StubMarket(["THYAO.IS"]), portfolio)  # type: ignore[arg-type]

    return client.portal.call(_run)


def test_dividend_is_internal_return_not_external_flow(client: TestClient) -> None:
    # 10.000 TL hisse + 1.000 nakit; 7 Ağu'da 110 TL temettü → TWR = +%1.
    pid = _portfolio_with_flows(
        client, cash=1_110.0, flows=[("DIVIDEND", 110.0, datetime(2026, 8, 7, 10))]
    )
    values, flows = _history(client, pid)
    from services.analytics.performance import time_weighted_return

    twr, _ = time_weighted_return(values, flows)
    assert float(flows.abs().sum()) == pytest.approx(0.0)
    assert twr == pytest.approx(0.01, abs=1e-9)


def test_fee_reduces_twr(client: TestClient) -> None:
    pid = _portfolio_with_flows(client, cash=945.0, flows=[("FEE", 55.0, datetime(2026, 8, 7, 10))])
    values, flows = _history(client, pid)
    from services.analytics.performance import time_weighted_return

    twr, _ = time_weighted_return(values, flows)
    assert float(flows.abs().sum()) == pytest.approx(0.0)
    assert twr == pytest.approx(-0.005, abs=1e-9)


def test_deposit_is_external_and_neutral_for_twr(client: TestClient) -> None:
    pid = _portfolio_with_flows(
        client, cash=6_000.0, flows=[("DEPOSIT", 5_000.0, datetime(2026, 8, 6, 10))]
    )
    values, flows = _history(client, pid)
    from services.analytics.performance import time_weighted_return

    twr, _ = time_weighted_return(values, flows)
    assert float(flows.sum()) == pytest.approx(5_000.0)
    assert twr == pytest.approx(0.0, abs=1e-12)


def test_saturday_deposit_rolls_forward_to_monday(client: TestClient) -> None:
    # Cumartesi 8 Ağu yatırma: kaybolmamalı, Pazartesi 10 Ağu'ya taşınmalı.
    pid = _portfolio_with_flows(
        client, cash=6_000.0, flows=[("DEPOSIT", 5_000.0, datetime(2026, 8, 8, 11))]
    )
    values, flows = _history(client, pid)
    assert float(flows.sum()) == pytest.approx(5_000.0)
    assert float(flows.loc["2026-08-10"]) == pytest.approx(5_000.0)
    assert float(values.iloc[0]) == pytest.approx(11_000.0)
    assert float(values.loc["2026-08-07"]) == pytest.approx(11_000.0)
    assert float(values.loc["2026-08-10"]) == pytest.approx(16_000.0)


def test_flow_after_last_price_day_is_valued_on_last_day(client: TestClient) -> None:
    # Fiyat verisi 14 Ağu'da bitiyor; 20 Ağu yatırma son değerleme gününe eklenir.
    pid = _portfolio_with_flows(
        client, cash=6_000.0, flows=[("DEPOSIT", 5_000.0, datetime(2026, 8, 20, 9))]
    )
    values, flows = _history(client, pid)
    assert float(values.iloc[0]) == pytest.approx(11_000.0)
    assert float(flows.iloc[-1]) == pytest.approx(5_000.0)


def test_performance_and_report_endpoints_agree(client: TestClient) -> None:
    cid = create_customer(client)["id"]
    pid = create_portfolio(client, cid)["id"]
    perf = client.get(f"/api/v1/portfolios/{pid}/performance").json()
    rep = client.get(f"/api/v1/portfolios/{pid}/report").json()
    # v2 denetim turu 1: Sharpe'ta bugünkü değil, dönemde geçerli faizlerin ortalaması.
    market = client.app.state.container.market  # type: ignore[attr-defined]
    start, end = rep["period"]["start"], rep["period"]["end"]
    rf = market.mean_risk_free_rate(pd.Timestamp(start), pd.Timestamp(end))
    assert rf > 0
    assert perf["risk_free_rate"] == pytest.approx(rf)
    assert rep["tear_sheet"]["risk_free_rate"] == pytest.approx(rf)
    assert perf["sharpe"] == pytest.approx(rep["tear_sheet"]["sharpe"])
    assert perf["twr_cumulative"] == pytest.approx(rep["twr_cumulative"])
    assert perf["twr_annualized"] == rep["twr_annualized"]  # aynı motor → birebir aynı
    assert perf["period"] == rep["period"]


def test_report_labels_cumulative_and_annualized_metrics(client: TestClient) -> None:
    cid = create_customer(client)["id"]
    pid = create_portfolio(client, cid)["id"]
    rep = client.get(f"/api/v1/portfolios/{pid}/report").json()
    for key in ("twr_cumulative", "twr_annualized", "mwr_annualized", "period"):
        assert key in rep, key
    assert {"start", "end", "days", "years"} <= set(rep["period"])
    assert "twr" not in rep and "mwr" not in rep  # belirsiz eski adlar kalktı


def test_cash_flow_api_updates_cash_and_classifies(client: TestClient) -> None:
    cid = create_customer(client, email="akis@example.com")["id"]
    pid = create_portfolio(client, cid, cash=1_000.0, holdings={"THYAO.IS": 100.0})["id"]
    div = client.post(
        f"/api/v1/portfolios/{pid}/cash-flows",
        json={"kind": "DIVIDEND", "amount": 110.0, "occurred_at": "2026-08-07T10:00:00"},
    )
    assert div.status_code == 201, div.text
    assert div.json()["cash_after"] == pytest.approx(1_110.0)
    assert div.json()["external"] is False
    too_much = client.post(
        f"/api/v1/portfolios/{pid}/cash-flows", json={"kind": "FEE", "amount": 1e9}
    )
    assert too_much.status_code == 409
    bad = client.post(f"/api/v1/portfolios/{pid}/cash-flows", json={"kind": "GIFT", "amount": 1})
    assert bad.status_code == 422
    values, flows = _history(client, pid)
    from services.analytics.performance import time_weighted_return

    twr, _ = time_weighted_return(values, flows)
    assert twr == pytest.approx(0.01, abs=1e-9)
    listed = client.get(f"/api/v1/portfolios/{pid}/cash-flows").json()
    assert [f["kind"] for f in listed] == ["DIVIDEND"]


def test_cash_correction_is_recorded_as_external_flow(client: TestClient) -> None:
    cid = create_customer(client, email="duzeltme@example.com")["id"]
    pid = create_portfolio(client, cid, cash=1_000.0, holdings={"THYAO.IS": 100.0})["id"]
    assert client.put(f"/api/v1/portfolios/{pid}", json={"cash": 6_000.0}).status_code == 200
    listed = client.get(f"/api/v1/portfolios/{pid}/cash-flows").json()
    assert listed[-1]["kind"] == "DEPOSIT" and listed[-1]["amount"] == pytest.approx(5_000.0)
    assert listed[-1]["external"] is True


def test_unknown_flow_kind_is_rejected() -> None:
    from datetime import date

    from services.analytics.engine import FlowEvent

    with pytest.raises(ValueError):
        _ = FlowEvent(date(2026, 1, 1), "GIFT", 1.0).cash_delta


def test_valuation_day_rolls_forward_and_clamps() -> None:
    from datetime import date

    from services.analytics.engine import valuation_day

    assert valuation_day(date(2026, 8, 1), DAYS) is None  # pencereden önce
    assert valuation_day(date(2026, 8, 8), DAYS) == pd.Timestamp("2026-08-10")  # Cmt → Pzt
    assert valuation_day(date(2026, 8, 12), DAYS) == pd.Timestamp("2026-08-12")
    assert valuation_day(date(2026, 9, 1), DAYS) == DAYS[-1]  # son günden sonra


def test_weekend_trade_is_replayed() -> None:
    from datetime import date

    from services.analytics.engine import TradeEvent, reconstruct

    prices = pd.DataFrame({"A": [10.0] * 5 + [12.0] * 5}, index=DAYS)
    # Cumartesi 8 Ağu 100 adet A alındı (1.000 + 5 maliyet); bugün 100 A + 0 nakit.
    buy = TradeEvent(date(2026, 8, 8), "A", "BUY", 100.0, 1_000.0, 5.0)
    values, flows = reconstruct(prices, {"A": 100.0}, 0.0, [buy], [])
    assert float(values.iloc[0]) == pytest.approx(1_005.0)  # alımdan önce yalnız nakit
    assert float(values.loc["2026-08-10"]) == pytest.approx(1_200.0)
    assert float(flows.abs().sum()) == 0.0


def test_summary_does_not_annualize_short_periods() -> None:
    from services.analytics.engine import summarize

    values = pd.Series(100.0 * 1.001 ** np.arange(len(DAYS)), index=DAYS)
    out = summarize(values, pd.Series(0.0, index=DAYS), risk_free_rate=0.40)
    assert out["twr_annualized"] is None and out["mwr_annualized"] is None
    assert out["period"]["annualized"] is False
    assert out["twr_cumulative"] == pytest.approx(1.001 ** (len(DAYS) - 1) - 1)
    assert out["tear_sheet"]["cagr"] is None
    assert out["tear_sheet"]["risk_free_rate"] == 0.40


def test_summary_annualizes_multi_year_consistently() -> None:
    from services.analytics.engine import summarize

    idx = pd.bdate_range("2022-01-03", "2024-12-31")
    values = pd.Series(100.0 * 1.0004 ** np.arange(len(idx)), index=idx)
    out = summarize(values, pd.Series(0.0, index=idx), risk_free_rate=0.30)
    years = out["period"]["years"]
    assert out["twr_annualized"] == pytest.approx((1 + out["twr_cumulative"]) ** (1 / years) - 1)
    assert out["tear_sheet"]["cagr"] == pytest.approx(out["twr_annualized"])
    assert out["mwr_annualized"] == pytest.approx(out["twr_annualized"], abs=1e-3)  # akış yok


def test_value_curve_starts_when_funded(client: TestClient) -> None:
    cid = create_customer(client, email="egri@example.com")["id"]
    pid = create_portfolio(client, cid, cash=300_000.0, holdings={})["id"]
    prop = client.post(f"/api/v1/advisor/rebalance/{pid}", params={"customer_id": cid}).json()
    client.post(f"/api/v1/proposals/{prop['proposal_id']}/approve")
    curve = client.get(f"/api/v1/portfolios/{pid}/report").json()["value_curve"]
    assert curve and all(point["value"] > 0 for point in curve)


def test_tear_sheet_infers_observation_frequency_from_dates() -> None:
    from services.analytics.performance import infer_periods_per_year, tear_sheet

    idx = pd.bdate_range("2021-01-01", "2025-12-31")  # yılda ~261 iş günü
    ppy = infer_periods_per_year(idx)
    assert 259 < ppy < 263
    r = pd.Series(0.001, index=idx)
    years = len(idx) / ppy
    assert tear_sheet(r)["cagr"] == pytest.approx(1.001 ** (len(idx) / years) - 1, rel=1e-9)
    monthly = pd.date_range("2020-01-31", periods=24, freq="ME")
    assert infer_periods_per_year(monthly) == pytest.approx(12.0, rel=0.01)
