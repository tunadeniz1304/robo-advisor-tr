"""FastAPI dependencies: container access, authentication and authorization.

Ownership rules (enforced here, tested in ``tests/test_auth.py``):
    * ``admin``    — every customer/portfolio.
    * ``danisman`` — customers whose ``advisor_user_id`` is the advisor.
    * ``musteri``  — only the customer linked to the user account.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_session
from core.security import TokenError, decode_token

_bearer = HTTPBearer(auto_error=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@dataclass(frozen=True)
class CurrentUser:
    """Authenticated principal extracted from the access token."""

    id: int
    role: str
    customer_id: int | None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_advisor(self) -> bool:
        return self.role == "danisman"

    @property
    def is_customer(self) -> bool:
        return self.role == "musteri"


def actor_of(user: CurrentUser) -> str:
    """Audit actor string of an authenticated user."""
    return f"user:{user.id}"


def get_container(request: Request) -> Any:
    """Return the application service container."""
    return request.app.state.container


def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser:
    """Resolve and validate the bearer access token.

    Raises:
        HTTPException: 401 when the token is missing or invalid.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Kimlik doğrulama gerekli.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    secret = request.app.state.settings.jwt_secret
    try:
        payload = decode_token(credentials.credentials, secret=secret, expected_type="access")
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    cid = payload.get("cid")
    return CurrentUser(
        id=int(payload["sub"]), role=str(payload["role"]), customer_id=int(cid) if cid else None
    )


UserDep = Annotated[CurrentUser, Depends(get_current_user)]


def require_roles(*roles: str) -> Any:
    """Dependency factory restricting an endpoint to the given roles."""

    def _check(user: UserDep) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bu işlem için yetkiniz yok.",
            )
        return user

    return Depends(_check)


def forbidden() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail="Bu kayda erişim yetkiniz yok."
    )


def can_access_customer(user: CurrentUser, customer: Any) -> bool:
    """Whether ``user`` may read/act on ``customer`` (ORM object)."""
    if user.is_admin:
        return True
    if user.is_advisor:
        return getattr(customer, "advisor_user_id", None) == user.id
    return user.customer_id is not None and user.customer_id == customer.id


async def load_customer_checked(session: AsyncSession, user: CurrentUser, customer_id: int) -> Any:
    """Fetch a customer and enforce access (404 / 403)."""
    from models import Customer

    customer = await session.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail=f"Customer {customer_id} bulunamadı.")
    if not can_access_customer(user, customer):
        raise forbidden()
    return customer


async def load_portfolio_checked(
    session: AsyncSession, user: CurrentUser, portfolio_id: int
) -> Any:
    """Fetch a portfolio and enforce access through its owner (404 / 403)."""
    from models import Customer, Portfolio

    portfolio = await session.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail=f"Portfolio {portfolio_id} bulunamadı.")
    customer = await session.get(Customer, portfolio.customer_id)
    if customer is None or not can_access_customer(user, customer):
        raise forbidden()
    return portfolio


__all__ = [
    "CurrentUser",
    "SessionDep",
    "UserDep",
    "can_access_customer",
    "get_container",
    "get_current_user",
    "load_customer_checked",
    "load_portfolio_checked",
    "require_roles",
]
