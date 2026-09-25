"""v2 audit findings C.13–C.17: production hardening.

* C.13 — prod refuses to start without a PII key or with a weak JWT secret.
* C.14 — ``/llm/status`` needs authentication (host only for staff) and
  ``/metrics`` is private by default.
* C.15 — portfolio locks and rate limits can be shared across processes
  (Redis; fakeredis in tests).
* C.16 — critical actions re-check the role in the DB; foreign resources
  answer 404 (never 403) so IDs cannot be probed.
* C.17 — stricter mypy configuration.
"""

from __future__ import annotations

import asyncio
import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.app import create_app
from core.config import ConfigurationError
from core.security import hash_password
from tests.conftest import (
    FakeMarketSource,
    auth_headers,
    create_customer,
    create_portfolio,
    login,
    make_settings,
)

STRONG_JWT = "Zq8#vT2!mK9pL4@xR7wN3$bH6yJ1&cF5dG0sA-uE"
FERNET_KEY = "o0l3m8ZC7cKQy4c9Yp2ZfVh7aJq3s6R1bW8nT5eX2kE="


def test_prod_without_pii_key_refuses_to_start(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path, environment="prod", jwt_secret=STRONG_JWT, pii_encryption_key=None
    )
    with pytest.raises(ConfigurationError, match="PII_ENCRYPTION_KEY"):
        create_app(settings=settings, market_source=FakeMarketSource())


@pytest.mark.parametrize(
    "secret", ["short-secret", "a" * 64, "dev-only-jwt-secret-change-me-32bytes-min"]
)
def test_prod_rejects_weak_jwt_secret(tmp_path: Path, secret: str) -> None:
    settings = make_settings(
        tmp_path, environment="prod", jwt_secret=secret, pii_encryption_key=FERNET_KEY
    )
    with pytest.raises(ConfigurationError, match="JWT_SECRET"):
        create_app(settings=settings, market_source=FakeMarketSource())


def test_prod_with_strong_secrets_starts(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path, environment="prod", jwt_secret=STRONG_JWT, pii_encryption_key=FERNET_KEY
    )
    app = create_app(settings=settings, market_source=FakeMarketSource())
    with TestClient(app) as c:
        assert c.get("/health/live").status_code == 200


def test_llm_status_requires_auth_and_hides_host_from_customers(
    client: TestClient, anon_client: TestClient
) -> None:
    assert anon_client.get("/api/v1/llm/status").status_code == 401
    staff = client.get("/api/v1/llm/status").json()
    assert "base_url_host" in staff and "mode" in staff
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "username": "llmmusteri",
            "password": "Musteri!2345",
            "full_name": "LLM Müşteri",
            "email": "llm.musteri@example.com",
        },
    ).json()
    body = client.get("/api/v1/llm/status", headers=auth_headers(reg["access_token"])).json()
    assert body["mode"] in {"live", "demo"}
    assert "base_url_host" not in body


def test_metrics_private_by_default(anon_client: TestClient) -> None:
    assert anon_client.get("/metrics").status_code == 404


def test_metrics_token(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, metrics_token="metrics-token-123456")
    app = create_app(settings=settings, market_source=FakeMarketSource())
    with TestClient(app) as c:
        assert c.get("/metrics").status_code == 404
        ok = c.get("/metrics", headers={"Authorization": "Bearer metrics-token-123456"})
        assert ok.status_code == 200 and "http_requests_total" in ok.text


def test_metrics_public_flag(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, metrics_public=True)
    app = create_app(settings=settings, market_source=FakeMarketSource())
    with TestClient(app) as c:
        assert c.get("/metrics").status_code == 200


def _add_user(client: TestClient, username: str, role: str) -> int:
    from core.database import session_factory
    from models import User

    async def _make() -> int:
        async with session_factory() as s:
            user = User(username=username, password_hash=hash_password("Parola!2345"), role=role)
            s.add(user)
            await s.commit()
            return int(user.id)

    return int(client.portal.call(_make))


def _set_role(client: TestClient, user_id: int, role: str) -> None:
    from sqlalchemy import update

    from core.database import session_factory
    from models import User

    async def _upd() -> None:
        async with session_factory() as s:
            await s.execute(update(User).where(User.id == user_id).values(role=role))
            await s.commit()

    client.portal.call(_upd)


def test_demoted_user_loses_staff_access_with_old_token(client: TestClient) -> None:
    uid = _add_user(client, "eski.danisman", "danisman")
    token = login(client, "eski.danisman", "Parola!2345")
    h = auth_headers(token)
    assert client.get("/api/v1/audit", headers=h).status_code == 200
    _set_role(client, uid, "musteri")
    assert client.get("/api/v1/audit", headers=h).status_code in {401, 403}


def test_demoted_admin_cannot_approve_with_old_token(client: TestClient) -> None:
    uid = _add_user(client, "eski.admin", "admin")
    h = auth_headers(login(client, "eski.admin", "Parola!2345"))
    cid = create_customer(client, email="demote@example.com")["id"]
    pid = create_portfolio(client, cid, cash=300_000.0, holdings={})["id"]
    prop = client.post(f"/api/v1/advisor/rebalance/{pid}", params={"customer_id": cid}).json()
    _set_role(client, uid, "musteri")
    resp = client.post(f"/api/v1/proposals/{prop['proposal_id']}/approve", headers=h)
    assert resp.status_code in {401, 403, 404}


def test_foreign_resources_answer_404_not_403(client: TestClient) -> None:
    other = create_customer(client, email="yabanci@example.com")
    other_pf = create_portfolio(client, other["id"])
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "username": "merakli",
            "password": "Merakli!2345",
            "full_name": "Meraklı Müşteri",
            "email": "merakli@example.com",
        },
    ).json()
    h = auth_headers(reg["access_token"])
    for path in (
        f"/api/v1/customers/{other['id']}",
        f"/api/v1/portfolios/{other_pf['id']}",
        f"/api/v1/portfolios/{other_pf['id']}/report",
        f"/api/v1/portfolios/{other_pf['id']}/valuation",
    ):
        existing = client.get(path, headers=h)
        missing = client.get(
            path.replace(str(other_pf["id"]), "987654").replace(str(other["id"]), "987654"),
            headers=h,
        )
        assert existing.status_code == 404, path
        assert missing.status_code == 404, path


def test_redis_lock_excludes_across_instances() -> None:
    fakeredis = pytest.importorskip("fakeredis")
    from core.locks import build_lock_manager

    server = fakeredis.FakeServer()
    a = build_lock_manager("redis", redis_client=fakeredis.FakeAsyncRedis(server=server))
    b = build_lock_manager("redis", redis_client=fakeredis.FakeAsyncRedis(server=server))
    order: list[str] = []

    async def worker(mgr, name: str) -> None:  # noqa: ANN001
        async with mgr.lock("portfolio:1"):
            order.append(f"{name}-in")
            await asyncio.sleep(0.05)
            order.append(f"{name}-out")

    async def main() -> None:
        await asyncio.gather(worker(a, "a"), worker(b, "b"))

    asyncio.run(main())
    # Kritik bölümler iç içe geçmemeli: in/out çiftleri ardışık.
    assert order[0][:1] == order[1][:1] and order[2][:1] == order[3][:1]


def test_memory_lock_backend_is_default() -> None:
    from core.locks import MemoryLockManager, build_lock_manager

    assert isinstance(build_lock_manager("memory"), MemoryLockManager)


def test_rate_limit_shared_across_instances_with_redis_storage() -> None:
    fakeredis = pytest.importorskip("fakeredis")
    from core.middleware import RateLimiter

    server = fakeredis.FakeServer()
    a = RateLimiter("2/minute", redis_client=fakeredis.FakeRedis(server=server))
    b = RateLimiter("2/minute", redis_client=fakeredis.FakeRedis(server=server))
    assert a.hit("ip:1") and b.hit("ip:1")
    assert not a.hit("ip:1") and not b.hit("ip:1")
    assert a.hit("ip:2")  # anahtar bazında


def test_two_app_instances_execute_a_proposal_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fakeredis = pytest.importorskip("fakeredis")
    import threading

    import core.redis_client as rc

    server = fakeredis.FakeServer()
    monkeypatch.setattr(rc, "async_client", lambda url: fakeredis.FakeAsyncRedis(server=server))
    monkeypatch.setattr(rc, "sync_client", lambda url: fakeredis.FakeRedis(server=server))
    settings = make_settings(
        tmp_path,
        lock_backend="redis",
        rate_limit_storage="redis",
        redis_url="redis://shared:6379/0",
    )
    app_a = create_app(settings=settings, market_source=FakeMarketSource())
    app_b = create_app(settings=settings, market_source=FakeMarketSource())
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        ca.headers.update(auth_headers(login(ca)))
        cb.headers.update(auth_headers(login(cb)))
        cid = create_customer(ca, email="ikiz@example.com")["id"]
        pid = create_portfolio(ca, cid, cash=300_000.0, holdings={})["id"]
        prop = ca.post(f"/api/v1/advisor/rebalance/{pid}", params={"customer_id": cid}).json()
        results: list[int] = []

        def approve(c: TestClient) -> None:
            results.append(c.post(f"/api/v1/proposals/{prop['proposal_id']}/approve").status_code)

        threads = [threading.Thread(target=approve, args=(c,)) for c in (ca, cb)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert all(code in {200, 409} for code in results), results
        txs = ca.get("/api/v1/transactions", params={"portfolio_id": pid}).json()
        buys = [t for t in txs if t["reason"] == "rebalance"]
        assert buys, "öneri yürütülmedi"
        per_symbol: dict[str, int] = {}
        for t in buys:
            per_symbol[t["ticker"]] = per_symbol.get(t["ticker"], 0) + 1
        assert max(per_symbol.values()) == 1, per_symbol  # çift yürütme yok
        assert app_a.state.container.locks.backend == "redis"


def test_mypy_config_is_strict() -> None:
    cfg = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["tool"]["mypy"]
    assert cfg["check_untyped_defs"] is True
    strict = [o for o in cfg.get("overrides", []) if o.get("disallow_untyped_defs") is True]
    modules = {m for o in strict for m in o["module"]}
    assert {"services.*", "core.*", "llm.*"} <= modules


def test_checkpointer_uses_postgres_when_database_is_postgres(tmp_path: Path) -> None:
    from agents.graph import is_postgres_url, postgres_dsn
    from core.container import checkpoint_target

    pg = make_settings(tmp_path, database_url="postgresql+asyncpg://u:p@db:5432/advisor")
    assert checkpoint_target(pg) == "postgresql+asyncpg://u:p@db:5432/advisor"
    assert is_postgres_url(checkpoint_target(pg) or "")
    assert (
        postgres_dsn("postgresql+asyncpg://u:p@db:5432/advisor")
        == "postgresql://u:p@db:5432/advisor"
    )
    sqlite = make_settings(tmp_path, checkpoint_db=str(tmp_path / "cp.sqlite"))
    assert checkpoint_target(sqlite) == str(tmp_path / "cp.sqlite")
    assert not is_postgres_url(str(tmp_path / "cp.sqlite"))


def test_redis_backend_requires_url(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="REDIS_URL"):
        create_app(make_settings(tmp_path, lock_backend="redis"), market_source=FakeMarketSource())


def test_forwarded_for_is_ignored_unless_trusted(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, rate_limit_default="2/minute")
    app = create_app(settings, market_source=FakeMarketSource())
    with TestClient(app) as c:
        codes = [
            c.get("/api/v1/health", headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code
            for i in range(3)
        ]
    assert codes == [200, 200, 429]  # sahte başlıkla limit aşılamaz
