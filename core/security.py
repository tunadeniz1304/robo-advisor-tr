"""Authentication primitives: password hashing and JWT tokens.

* Passwords are hashed with bcrypt (cost 12; 4 in tests via ``BCRYPT_ROUNDS``).
* Access and refresh tokens are HS256 JWTs carrying ``sub`` (user id),
  ``role``, ``cid`` (customer id for ``musteri``) and ``typ``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

ROLES = ("musteri", "danisman", "admin")
_ALGORITHM = "HS256"


class TokenError(ValueError):
    """Raised for invalid, expired or wrong-type tokens."""


def hash_password(password: str) -> str:
    """Hash a password with bcrypt."""
    rounds = int(os.getenv("BCRYPT_ROUNDS", "12"))
    digest = bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt(rounds=rounds))
    return digest.decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    """Constant-time password verification."""
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], hashed.encode("ascii"))
    except ValueError:
        return False


def create_token(
    *,
    user_id: int,
    role: str,
    customer_id: int | None,
    secret: str,
    ttl_minutes: int,
    token_type: str = "access",
) -> str:
    """Create a signed JWT.

    Args:
        user_id: Subject (user primary key).
        role: One of :data:`ROLES`.
        customer_id: Linked customer for ``musteri`` users.
        secret: HMAC secret.
        ttl_minutes: Lifetime in minutes.
        token_type: ``access`` or ``refresh``.

    Returns:
        The encoded token string.
    """
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "cid": customer_id,
        "typ": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl_minutes)).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


def decode_token(token: str, *, secret: str, expected_type: str = "access") -> dict[str, Any]:
    """Decode and validate a JWT.

    Raises:
        TokenError: When the token is invalid, expired or of another type.
    """
    try:
        payload = jwt.decode(token, secret, algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Oturum süresi doldu.") from exc
    except jwt.PyJWTError as exc:
        raise TokenError("Geçersiz kimlik doğrulama belirteci.") from exc
    if payload.get("typ") != expected_type:
        raise TokenError("Belirteç türü uygun değil.")
    if payload.get("role") not in ROLES:
        raise TokenError("Geçersiz rol.")
    return payload


__all__ = [
    "ROLES",
    "TokenError",
    "create_token",
    "decode_token",
    "hash_password",
    "verify_password",
]
