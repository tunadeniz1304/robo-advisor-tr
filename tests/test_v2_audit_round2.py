"""v2 bağımsız denetim turu 2 bulgularının regresyon testleri."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from core.policy import get_policy
from services.analytics.performance import tear_sheet
from services.analytics.walkforward import trim_for_test
from services.market_data.service import MarketDataService
from services.market_data.sources import ChainedSource, SnapshotSource
from services.rebalancing.costs import TaxModel


def _market() -> MarketDataService:
    return MarketDataService(ChainedSource(None, SnapshotSource(), mode="snapshot"))


def test_risk_free_rate_is_compounded_not_the_quoted_simple_rate() -> None:
    market = _market()
    quoted = market.policy_rate()
    assert market.risk_free_rate() == pytest.approx((1 + quoted / 365) ** 365 - 1)
    assert market.risk_free_rate() > quoted


def test_cash_fund_accruing_the_policy_rate_has_near_zero_sharpe() -> None:
    """Günlük politika faizi tahakkuk eden bir fonun Sharpe'ı ~0 olmalı (eskiden ~3,7)."""
    market = _market()
    idx = pd.bdate_range("2022-01-03", "2025-12-31")
    rates = np.array([market.policy_rate_at(d) for d in idx])
    gaps = np.diff(np.r_[idx[0].toordinal() - 1, [d.toordinal() for d in idx]])
    daily = (1 + rates / 365) ** gaps - 1
    noise = np.random.default_rng(0).normal(0, 1e-4, len(idx))
    r = pd.Series(daily + noise, index=idx)
    rf = market.mean_risk_free_rate(idx[0], idx[-1])
    sharpe = tear_sheet(r, risk_free_rate=rf)["sharpe"]
    assert abs(sharpe) < 1.0


def test_withholding_base_is_net_of_sale_fees() -> None:
    lot = SimpleNamespace(
        id=1,
        symbol="TL_PPF",
        quantity_open=10,
        unit_cost=100.0,
        acquired_at=datetime.utcnow() - timedelta(days=30),
    )
    gross = TaxModel().estimate_sale("TL_PPF", 10, 200.0, [lot])
    net = TaxModel().estimate_sale("TL_PPF", 10, 200.0, [lot], fees=50.0)
    assert net.realized_gain == pytest.approx(gross.realized_gain - 50.0)
    assert net.tax < gross.tax


def test_default_lot_method_is_fifo() -> None:
    assert get_policy().rebalance["lot_method"] == "FIFO"


def test_walk_forward_test_period_is_calendar_years() -> None:
    idx = pd.bdate_range("2015-01-01", "2025-12-31")
    panel = pd.DataFrame({"a": 1.0}, index=idx)
    trimmed = trim_for_test(panel, 5.0, lead=10)
    first_test = trimmed.index[10]
    span = (trimmed.index[-1] - first_test).days / 365.25
    assert span == pytest.approx(5.0, abs=0.01)
