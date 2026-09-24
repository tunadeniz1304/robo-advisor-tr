"""Shared fixtures for the test suite.

The suite runs fully offline and never touches the real ``.env`` key:

    * ``LLM_MODE=demo`` is forced in the process environment before any
      settings are loaded (real env vars win over ``.env``).
    * :class:`FakeMarketSource` returns synthetic, reproducible price
      histories seeded with ``zlib.crc32`` (bug #10: ``hash()`` is salted per
      process, so the old seeds were not deterministic across runs).
    * :class:`DeterministicLLM` is the production demo client
      (:mod:`llm.demo`), re-exported here for backwards compatibility.

Each test gets its own temp-file SQLite database and an authenticated
:class:`fastapi.testclient.TestClient` (admin by default).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import zlib
from collections.abc import Iterator
from pathlib import Path

os.environ["LLM_MODE"] = "demo"
os.environ.setdefault("BCRYPT_ROUNDS", "4")
os.environ.setdefault("DATA_MODE", "snapshot")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core.app import create_app  # noqa: E402
from core.config import Settings  # noqa: E402
from llm.demo import DeterministicLLM  # noqa: E402,F401 - re-export
from services.market_service import MarketDataSource  # noqa: E402

ADMIN_USER = "admin"
ADMIN_PASS = "test-admin-pass"

# ---------------------------------------------------------------- doubles ---


def stable_seed(symbol: str) -> int:
    """Process-independent seed for a symbol (``zlib.crc32``)."""
    return zlib.crc32(symbol.encode("utf-8")) & 0xFFFFFFFF


class FakeMarketSource(MarketDataSource):
    """Deterministic price source; no network access.

    Generates ``n_days`` daily closes per symbol as a geometric random walk
    seeded by ``crc32(symbol)``, so every run yields identical data.
    """

    def __init__(
        self, symbols: list[str] | None = None, n_days: int = 260, start: float = 100.0
    ) -> None:
        self._symbols = symbols or []
        self._n_days = n_days
        self._start = start

    async def download_history(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        frames: dict[str, pd.DataFrame] = {}
        dates = pd.bdate_range(end=pd.Timestamp("2026-09-01"), periods=self._n_days)
        for sym in symbols:
            seed = stable_seed(sym)
            rng = np.random.default_rng(seed)
            drift = 0.0004 + (seed % 5) * 0.0002
            shocks = rng.normal(drift, 0.01, self._n_days)
            prices = self._start * np.exp(np.cumsum(shocks))
            frames[sym] = pd.DataFrame(
                {"Close": prices}, index=pd.DatetimeIndex(dates, name="Date")
            )
        return frames


# ---------------------------------------------------------------- database ---

_TEMPLATE_DIR = Path(tempfile.mkdtemp(prefix="advisor-test-template-"))
TEMPLATE_DB = _TEMPLATE_DIR / "template.db"


def _block_network() -> None:
    """Fail loudly if any test tries to open a non-loopback connection."""
    import socket

    real_connect = socket.socket.connect

    def guarded_connect(self: socket.socket, address: object) -> object:
        host = address[0] if isinstance(address, tuple) else str(address)
        if isinstance(host, str) and host not in {"127.0.0.1", "::1", "localhost"}:
            raise RuntimeError(f"Testlerde ağ erişimi yasak: {host}")
        return real_connect(self, address)  # type: ignore[arg-type]

    socket.socket.connect = guarded_connect  # type: ignore[method-assign]


def pytest_configure(config: pytest.Config) -> None:
    """Block the network and migrate one template database for the session."""
    from core.database import run_migrations

    if os.getenv("ALLOW_NETWORK_IN_TESTS") != "1":
        _block_network()
    run_migrations(f"sqlite+aiosqlite:///{TEMPLATE_DB.as_posix()}")


def pytest_unconfigure(config: pytest.Config) -> None:
    shutil.rmtree(_TEMPLATE_DIR, ignore_errors=True)


def _test_db_url(tmp_path: Path) -> str:
    target = tmp_path / "test.db"
    if not target.exists():
        shutil.copyfile(TEMPLATE_DB, target)
    return f"sqlite+aiosqlite:///{target.as_posix()}"


# ---------------------------------------------------------------- fixtures ---


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    """Settings for an isolated test app (demo LLM, temp DB, no scheduler)."""
    base: dict[str, object] = {
        "database_url": _test_db_url(tmp_path),
        "log_level": "ERROR",
        "log_json": False,
        "llm_mode": "demo",
        "environment": "test",
        "bootstrap_admin_username": ADMIN_USER,
        "bootstrap_admin_password": ADMIN_PASS,
        "checkpoint_db": None,
        "scheduler_enabled": False,
        "data_mode": "snapshot",
        "rate_limit_default": "10000/minute",
        "rate_limit_login": "1000/minute",
        "auto_migrate": False,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


def login(client: TestClient, username: str = ADMIN_USER, password: str = ADMIN_PASS) -> str:
    """Log in and return the access token."""
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def client(settings: Settings) -> Iterator[TestClient]:
    """Admin-authenticated client with an offline market source."""
    app = create_app(settings=settings, market_source=FakeMarketSource())
    with TestClient(app) as c:
        c.headers.update(auth_headers(login(c)))
        yield c


@pytest.fixture()
def anon_client(settings: Settings) -> Iterator[TestClient]:
    """Unauthenticated client."""
    app = create_app(settings=settings, market_source=FakeMarketSource())
    with TestClient(app, raise_server_exceptions=False) as c:
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
