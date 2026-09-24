"""Pydantic schemas for authentication."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    """Username/password login payload."""

    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=4, max_length=128)


class RefreshRequest(BaseModel):
    """Refresh token payload."""

    refresh_token: str = Field(min_length=10)


class TokenResponse(BaseModel):
    """Issued token pair."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    role: str
    customer_id: int | None = None


class RegisterRequest(BaseModel):
    """Self sign-up of a retail customer (``musteri``)."""

    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=2, max_length=160)
    email: EmailStr
    initial_cash: float = Field(default=0.0, ge=0.0, le=1_000_000_000)


class MeResponse(BaseModel):
    """Current user profile."""

    id: int
    username: str
    role: str
    customer_id: int | None
    display_name: str | None
