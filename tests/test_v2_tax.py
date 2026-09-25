"""v2 §2.27: lot-based tax engine (FIFO / HIFO), realised P&L and harvesting."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from services.tax_engine import LotBook, harvest_simulation, realized_summary, replay
from tests.conftest import create_customer, create_portfolio

D1, D2, SALE = date(2025, 1, 10), date(2025, 6, 10), date(2025, 9, 1)


def _book(method: str) -> LotBook:
    book = LotBook(method)
    book.buy("TL_PPF", 100, 10.0, 10.0, D1)  # birim maliyet 10,10 (komisyon dahil)
    book.buy("TL_PPF", 100, 12.0, 0.0, D2)
    return book


def test_fifo_sale_gain_and_tax() -> None:
    res = _book("FIFO").sell("TL_PPF", 150, 15.0, 15.0, SALE)
    # 100 × (15 − 10,10 − 0,10) + 50 × (15 − 12 − 0,10)
    assert res.gain == pytest.approx(480.0 + 145.0)
    assert res.tax() == pytest.approx(0.175 * 625.0)
    assert [s.quantity for s in res.slices] == [100, 50]


def test_hifo_sells_highest_cost_first_and_lowers_tax() -> None:
    fifo = _book("FIFO").sell("TL_PPF", 150, 15.0, 15.0, SALE)
    hifo_book = _book("HIFO")
    hifo = hifo_book.sell("TL_PPF", 150, 15.0, 15.0, SALE)
    assert hifo.gain == pytest.approx(100 * 2.9 + 50 * 4.8)
    assert hifo.tax() < fifo.tax()
    left = hifo_book.open_lots()
    assert len(left) == 1 and left[0].unit_cost == pytest.approx(10.1) and left[0].quantity == 50


def test_dry_run_does_not_consume_and_untracked_quantity_has_no_gain() -> None:
    book = _book("FIFO")
    res = book.sell("TL_PPF", 250, 15.0, 0.0, SALE, dry_run=True)
    assert res.untracked_quantity == pytest.approx(50)
    assert sum(lot.quantity for lot in book.open_lots()) == pytest.approx(200)


def test_losses_offset_gains_within_a_class_and_tax_is_not_negative() -> None:
    book = LotBook("FIFO")
    book.buy("TL_PPF", 100, 20.0, 0.0, D1)
    res = book.sell("TL_PPF", 100, 15.0, 0.0, SALE)
    assert res.gain == pytest.approx(-500.0) and res.tax() == 0.0


def test_replay_and_realized_summary_by_year() -> None:
    trades = [
        {
            "symbol": "TL_PPF",
            "side": "BUY",
            "quantity": 100,
            "price": 10.0,
            "fees": 0.0,
            "when": datetime(2024, 1, 5),
        },
        {
            "symbol": "TL_PPF",
            "side": "SELL",
            "quantity": 40,
            "price": 11.0,
            "fees": 0.0,
            "when": datetime(2024, 12, 5),
        },
        {
            "symbol": "TL_PPF",
            "side": "SELL",
            "quantity": 60,
            "price": 13.0,
            "fees": 0.0,
            "when": datetime(2025, 2, 5),
        },
    ]
    book, realized = replay(trades, "FIFO")
    assert not book.open_lots()
    y24 = realized_summary(realized, 2024)
    y25 = realized_summary(realized, 2025)
    assert y24["total_gain"] == pytest.approx(40.0) and y25["total_gain"] == pytest.approx(180.0)
    assert y25["estimated_tax"] == pytest.approx(0.175 * 180.0)


def test_harvest_saving_is_capped_by_same_class_gains() -> None:
    book = LotBook("FIFO")
    book.buy("TL_PPF", 100, 10.0, 0.0, D1)
    book.buy("TL_TAHVIL", 100, 10.0, 0.0, D1)
    realized = book.sell("TL_PPF", 50, 12.0, 0.0, date(2025, 3, 1)).slices  # +100 kâr
    out = harvest_simulation(
        book, {"TL_PPF": 4.0, "TL_TAHVIL": 9.0}, realized, today=date(2025, 9, 1)
    )
    ppf = out["by_class"]["para_piyasasi"]
    assert ppf["unrealized_loss"] == pytest.approx(300.0)
    assert ppf["offsettable"] == pytest.approx(100.0)
    assert ppf["estimated_saving"] == pytest.approx(17.5)
    assert out["by_class"]["tl_tahvil"]["estimated_saving"] == 0.0  # o sınıfta kâr yok


def test_bad_method_rejected() -> None:
    with pytest.raises(ValueError):
        LotBook("LIFO")


def test_tax_api_endpoints(client: TestClient) -> None:
    cid = create_customer(client, email="vergi@example.com")["id"]
    pid = create_portfolio(client, cid, cash=100_000.0, holdings={})["id"]
    for price in (10.0, 12.0):
        r = client.post(
            "/api/v1/transactions",
            json={
                "portfolio_id": pid,
                "ticker": "TL_PPF",
                "side": "BUY",
                "quantity": 100,
                "price": price,
            },
        )
        assert r.status_code == 201, r.text
    lots = client.get(f"/api/v1/portfolios/{pid}/tax/lots", params={"method": "FIFO"}).json()
    assert [lot["quantity"] for lot in lots["lots"]] == [100, 100]
    sim = client.post(
        f"/api/v1/portfolios/{pid}/tax/simulate-sale", json={"symbol": "TL_PPF", "quantity": 150}
    ).json()
    assert set(sim["methods"]) == {"FIFO", "HIFO"}
    assert sim["methods"]["HIFO"]["gain"] <= sim["methods"]["FIFO"]["gain"]
    assert client.get(f"/api/v1/portfolios/{pid}/tax/realized").json()["total_gain"] == 0.0
    assert "by_class" in client.get(f"/api/v1/portfolios/{pid}/tax/harvest").json()
    assert (
        client.get(f"/api/v1/portfolios/{pid}/tax/lots", params={"method": "LIFO"}).status_code
        == 422
    )
