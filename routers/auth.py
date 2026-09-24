"""Authentication router: login, refresh, current user and self sign-up."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from core.config import Settings
from core.crypto import blind_index
from core.deps import SessionDep, UserDep
from core.logging import get_logger
from core.middleware import client_key
from core.security import TokenError, create_token, decode_token, hash_password, verify_password
from models import Customer, Portfolio, User
from schemas.auth import LoginRequest, MeResponse, RefreshRequest, RegisterRequest, TokenResponse

logger = get_logger("otonom.auth")
router = APIRouter(prefix="/auth", tags=["auth"])


def _issue(settings: Settings, user: User) -> TokenResponse:
    def _make(ttl: int, kind: str) -> str:
        return create_token(
            user_id=user.id,
            role=user.role,
            customer_id=user.customer_id,
            secret=settings.jwt_secret,
            ttl_minutes=ttl,
            token_type=kind,
        )

    return TokenResponse(
        access_token=_make(settings.jwt_access_ttl_minutes, "access"),
        refresh_token=_make(settings.jwt_refresh_ttl_minutes, "refresh"),
        expires_in=settings.jwt_access_ttl_minutes * 60,
        role=user.role,
        customer_id=user.customer_id,
    )


def _enforce_login_limit(request: Request) -> None:
    settings: Settings = request.app.state.settings
    limiter = request.app.state.rate_limiter
    if not limiter.hit(f"login:{client_key(request)}", settings.rate_limit_login):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Çok fazla giriş denemesi. Lütfen bir dakika sonra tekrar deneyin.",
        )


async def _authenticate(session: SessionDep, username: str, password: str) -> User:
    user = (
        await session.execute(select(User).where(User.username == username.strip()))
    ).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        logger.info("login_failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Kullanıcı adı veya şifre hatalı.",
        )
    logger.info("login_succeeded", user_id=user.id, role=user.role)
    return user


@router.post("/login", response_model=TokenResponse, summary="Kullanıcı girişi (JSON)")
async def login(request: Request, payload: LoginRequest, session: SessionDep) -> TokenResponse:
    """Authenticate with username/password and receive a token pair."""
    _enforce_login_limit(request)
    user = await _authenticate(session, payload.username, payload.password)
    return _issue(request.app.state.settings, user)


@router.post(
    "/token", response_model=TokenResponse, include_in_schema=True, summary="OAuth2 form girişi"
)
async def token(
    request: Request,
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: SessionDep,
) -> TokenResponse:
    """OAuth2 password flow (used by the Swagger UI "Authorize" button)."""
    _enforce_login_limit(request)
    user = await _authenticate(session, form.username, form.password)
    return _issue(request.app.state.settings, user)


@router.post("/refresh", response_model=TokenResponse, summary="Belirteç yenile")
async def refresh(request: Request, payload: RefreshRequest, session: SessionDep) -> TokenResponse:
    """Exchange a valid refresh token for a new token pair."""
    settings: Settings = request.app.state.settings
    try:
        claims = decode_token(
            payload.refresh_token, secret=settings.jwt_secret, expected_type="refresh"
        )
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    user = await session.get(User, int(claims["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Kullanıcı bulunamadı."
        )
    return _issue(settings, user)


@router.get("/me", response_model=MeResponse, summary="Oturumdaki kullanıcı")
async def me(user: UserDep, session: SessionDep) -> MeResponse:
    """Return the authenticated user's profile."""
    row = await session.get(User, user.id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Kullanıcı bulunamadı."
        )
    return MeResponse(
        id=row.id,
        username=row.username,
        role=row.role,
        customer_id=row.customer_id,
        display_name=row.display_name,
    )


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Bireysel müşteri kaydı (onboarding başlangıcı)",
)
async def register(
    request: Request, payload: RegisterRequest, session: SessionDep
) -> TokenResponse:
    """Create a ``musteri`` user with its customer record and a main portfolio."""
    _enforce_login_limit(request)
    exists = (
        await session.execute(select(User.id).where(User.username == payload.username))
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Bu kullanıcı adı alınmış."
        )
    email_taken = (
        await session.execute(
            select(Customer.id).where(Customer.email_hash == blind_index(str(payload.email)))
        )
    ).scalar_one_or_none()
    if email_taken is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Bu e-posta adresi zaten kayıtlı."
        )

    customer = Customer(full_name=payload.full_name, email=str(payload.email))
    session.add(customer)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Kayıt oluşturulamadı."
        ) from exc
    session.add(
        Portfolio(
            customer_id=customer.id,
            name="Ana Portföy",
            currency="TRY",
            cash=Decimal(str(payload.initial_cash)),
            holdings={},
        )
    )
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role="musteri",
        customer_id=customer.id,
        display_name=payload.full_name,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    logger.info("customer_registered", user_id=user.id, customer_id=customer.id)
    return _issue(request.app.state.settings, user)
