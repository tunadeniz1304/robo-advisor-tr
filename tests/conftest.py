"""Shared fixtures for the integration test suite.

The suite must run fully offline. Two deterministic test doubles are provided:

    * :class:`FakeMarketSource` — satisfies
      :class:`services.market_service.MarketDataSource` and returns synthetic,
      reproducible price histories (geometric random walk with a fixed seed).
    * :class:`DeterministicLLM` — satisfies :class:`llm.clients.LLMClient` and
      returns a fixed Turkish narrative. It is a hand-written deterministic
      stub used only for dependency injection in tests. It is **not**
      ``FakeListChatModel`` (which is banned in this project) and never
      appears in production code.

Each test gets its own temp-file SQLite database (file-backed because the
async engine opens several connections, which in-memory SQLite cannot serve
reliably) and a fresh :class:`fastapi.testclient.TestClient`.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from core.app import create_app
from core.config import Settings
from services.market_service import MarketDataSource

# ---------------------------------------------------------------- doubles ---


class FakeMarketSource(MarketDataSource):
    """Deterministic price source; no network access.

    Generates ``n_days`` daily closes per symbol as a geometric random walk
    seeded by the symbol, so every test run yields identical data.
    """

    def __init__(self, symbols: list[str], n_days: int = 120, start: float = 100.0) -> None:
        self._symbols = symbols
        self._n_days = n_days
        self._start = start
        self._rng = np.random.default_rng(seed=42)

    async def download_history(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        frames: dict[str, pd.DataFrame] = {}
        for sym in symbols:
            seed = abs(hash(sym)) % (2**32)
            rng = np.random.default_rng(seed)
            drift = 0.0004 + (seed % 5) * 0.0002
            shocks = rng.normal(drift, 0.01, self._n_days)
            prices = self._start * np.exp(np.cumsum(shocks))
            dates = pd.bdate_range(end=pd.Timestamp.today(), periods=self._n_days)
            frames[sym] = pd.DataFrame(
                {"Close": prices}, index=pd.DatetimeIndex(dates, name="Date")
            )
        return frames


class DeterministicLLM:
    """Hand-written deterministic LLM double for offline tests.

    Implements the same async interface as real clients
    (:class:`llm.clients.LLMClient`) and echoes a fixed, Turkish, sensible
    narrative — no network, no randomness.
    """

    def __init__(self, text: str | None = None) -> None:
        self._text = text or (
            "Hedef dağılım, müşterinin risk profiliyle uyumludur. Piyasa "
            "momentumu dikkate alındığında mevcut varlık dağılımının korunması "
            "ve dengeli bir şekilde yeniden dengelenmesi önerilir. Yatırım "
            "danışmanlığı değildir."
        )
        self.calls: list[tuple[str, str]] = []

    async def complete(
        self, *, system: str, user: str, max_tokens: int = 800, temperature: float = 0.4
    ) -> str:
        self.calls.append((system, user))
        return self._text


# ---------------------------------------------------------------- fixtures ---


def _test_db_path(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=_test_db_path(tmp_path),
        log_level="ERROR",
        log_json=False,
    )


@pytest.fixture()
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings=settings)
    with TestClient(app) as c:
        yield c


def create_customer(client: TestClient, **overrides: object) -> dict:
    """Helper: POST a customer and return the created body."""
    payload = {
        "full_name": "Ayşe Yılmaz",
        "email": "ayse.test@example.com",
        "investment_horizon_years": 10,
        "monthly_income": 45000.0,
        "declared_risk_tolerance": 4,
        "financial_goal": "Emeklilik birikimi",
    }
    payload.update(overrides)
    resp = client.post("/api/v1/customers", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def create_portfolio(client: TestClient, customer_id: int, **overrides: object) -> dict:
    """Helper: POST a portfolio for a customer and return the created body."""
    payload = {
        "customer_id": customer_id,
        "name": "Ana Portföy",
        "currency": "TRY",
        "cash": 250_000.0,
        "holdings": {"THYAO.IS": 100.0, "AKBNK.IS": 200.0, "ASELS.IS": 50.0},
    }
    payload.update(overrides)
    resp = client.post("/api/v1/portfolios", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()
