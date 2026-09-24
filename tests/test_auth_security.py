"""Authentication, authorization and HTTP hardening tests (P0.9)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from core.app import create_app
from core.security import create_token, decode_token, hash_password, verify_password
from tests.conftest import (
    ADMIN_PASS,
    FakeMarketSource,
    auth_headers,
    create_customer,
    create_portfolio,
    login,
    make_settings,
)


def _register(client: TestClient, username: str, email: str, cash: float = 100_000.0) -> dict:
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "password": "GucluSifre!1",
            "full_name": "Deneme Kişi",
            "email": email,
            "initial_cash": cash,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_password_hashing_roundtrip() -> None:
    hashed = hash_password("s3cret-pass")
    assert hashed != "s3cret-pass"
    assert verify_password("s3cret-pass", hashed)
    assert not verify_password("wrong", hashed)


def test_token_types_are_enforced() -> None:
    secret = "x" * 40
    refresh = create_token(
        user_id=1,
        role="admin",
        customer_id=None,
        secret=secret,
        ttl_minutes=5,
        token_type="refresh",
    )
    assert decode_token(refresh, secret=secret, expected_type="refresh")["sub"] == "1"
    try:
        decode_token(refresh, secret=secret, expected_type="access")
    except ValueError as exc:
        assert "tür" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("refresh token access olarak kabul edilmemeli")


def test_protected_endpoints_require_auth(anon_client: TestClient) -> None:
    for path in ("/api/v1/customers", "/api/v1/portfolios", "/api/v1/transactions", "/api/v1/runs"):
        resp = anon_client.get(path)
        assert resp.status_code == 401, path
        assert "correlation_id" in resp.json()
    # Açık uçlar
    assert anon_client.get("/health").status_code == 200
    assert anon_client.get("/api/v1/llm/status").status_code == 200


def test_login_failure_and_me(anon_client: TestClient) -> None:
    bad = anon_client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "yanlis-sifre"}
    )
    assert bad.status_code == 401
    token = login(anon_client)
    me = anon_client.get("/api/v1/auth/me", headers=auth_headers(token)).json()
    assert me["role"] == "admin" and me["username"] == "admin"


def test_refresh_flow(anon_client: TestClient) -> None:
    resp = anon_client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": ADMIN_PASS}
    )
    refreshed = anon_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": resp.json()["refresh_token"]}
    )
    assert refreshed.status_code == 200
    assert (
        anon_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": resp.json()["access_token"]}
        ).status_code
        == 401
    )


def test_customer_sees_only_own_data(client: TestClient) -> None:
    other = create_customer(client, email="baska@example.com")
    other_pf = create_portfolio(client, other["id"])

    reg = _register(client, "musteri1", "musteri1@example.com")
    h = auth_headers(reg["access_token"])
    own_cid = reg["customer_id"]

    listed = client.get("/api/v1/customers", headers=h).json()
    assert [c["id"] for c in listed] == [own_cid]
    assert client.get(f"/api/v1/customers/{other['id']}", headers=h).status_code == 403
    assert client.get(f"/api/v1/portfolios/{other_pf['id']}", headers=h).status_code == 403
    # Başkasının portföyünde işlem / rebalance tetikleyemez
    tx = client.post(
        "/api/v1/transactions",
        headers=h,
        json={
            "portfolio_id": other_pf["id"],
            "ticker": "THYAO.IS",
            "side": "BUY",
            "quantity": 1,
            "price": 10,
        },
    )
    assert tx.status_code == 403
    rb = client.post(
        f"/api/v1/advisor/rebalance/{other_pf['id']}",
        params={"customer_id": other["id"]},
        headers=h,
    )
    assert rb.status_code == 403
    # Müşteri silemez, nakit düzeltemez
    assert client.delete(f"/api/v1/customers/{own_cid}", headers=h).status_code == 403
    own_pf = client.get("/api/v1/portfolios", headers=h).json()[0]
    assert (
        client.put(f"/api/v1/portfolios/{own_pf['id']}", headers=h, json={"cash": 1e9}).status_code
        == 403
    )


def test_advisor_sees_only_assigned_customers(client: TestClient) -> None:
    from sqlalchemy import select  # noqa: F401 - yalnızca API üzerinden

    unassigned = create_customer(client, email="atanmamis@example.com")
    # Danışman kullanıcısını admin API yerine DB bootstrap ile oluşturmak yerine kayıt + rol güncellemesi

    from core.database import session_factory
    from models import User

    async def _make_advisor() -> None:
        async with session_factory() as s:
            s.add(
                User(
                    username="danisman1", password_hash=hash_password("Danisman!1"), role="danisman"
                )
            )
            await s.commit()

    client.portal.call(_make_advisor)
    h = auth_headers(login(client, "danisman1", "Danisman!1"))
    mine = client.post(
        "/api/v1/customers",
        headers=h,
        json={"full_name": "Atanmış Müşteri", "email": "atanmis@example.com"},
    ).json()
    ids = [c["id"] for c in client.get("/api/v1/customers", headers=h).json()]
    assert ids == [mine["id"]]
    assert client.get(f"/api/v1/customers/{unassigned['id']}", headers=h).status_code == 403


def test_security_headers_and_request_id(client: TestClient) -> None:
    resp = client.get("/api/v1/customers", headers={"X-Request-ID": "req-123"})
    assert resp.headers["X-Request-ID"] == "req-123"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'none'" in resp.headers["Content-Security-Policy"]


def test_cors_whitelist(client: TestClient) -> None:
    ok = client.options(
        "/api/v1/customers",
        headers={"Origin": "http://localhost:8000", "Access-Control-Request-Method": "GET"},
    )
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:8000"
    bad = client.options(
        "/api/v1/customers",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in bad.headers


def test_login_rate_limit(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, rate_limit_login="3/minute")
    app = create_app(settings, market_source=FakeMarketSource())
    with TestClient(app) as c:
        codes = [
            c.post("/api/v1/auth/login", json={"username": "yok", "password": "yyyyyy"}).status_code
            for _ in range(5)
        ]
    assert codes[:3] == [401, 401, 401]
    assert codes[3:] == [429, 429]


def test_global_rate_limit(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, rate_limit_default="2/minute")
    app = create_app(settings, market_source=FakeMarketSource())
    with TestClient(app) as c:
        codes = [c.get("/api/v1/llm/status").status_code for _ in range(4)]
    assert codes == [200, 200, 429, 429]


def test_500_does_not_leak_exception_text(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path), market_source=FakeMarketSource())

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("gizli-ic-detay: veritabani sifresi xyz")

    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get("/boom")
    assert resp.status_code == 500
    assert "gizli-ic-detay" not in resp.text
    assert resp.json()["correlation_id"]


def test_pii_encrypted_at_rest(client: TestClient) -> None:

    from sqlalchemy import text

    from core.database import session_factory

    create_customer(client, email="gizli@example.com", monthly_income=77777.0)

    async def _raw() -> tuple[str, str, str]:
        async with session_factory() as s:
            row = (
                await s.execute(text("SELECT email, email_hash, monthly_income FROM customers"))
            ).one()
            return row[0], row[1], row[2]

    email, email_hash, income = client.portal.call(_raw)
    assert "gizli@example.com" not in email and "77777" not in income
    assert len(email_hash) == 64
    # API çözer
    body = client.get("/api/v1/customers").json()[0]
    assert body["email"] == "gizli@example.com" and body["monthly_income"] == 77777.0
