"""Application-level PII encryption (Fernet) as transparent SQLAlchemy types.

* :class:`EncryptedString` / :class:`EncryptedDecimal` encrypt on write and
  decrypt on read, so ORM code keeps using plain ``str`` / ``Decimal``.
* :func:`blind_index` produces a keyed HMAC-SHA256 of a normalised value —
  used for the unique, searchable ``customers.email_hash`` column without
  storing the e-mail in clear text.

The key comes from ``PII_ENCRYPTION_KEY`` (a Fernet key). Only in the dev and
test environments may it be missing: a deterministic development key is then
derived and a warning is logged. In production the application refuses to
start without a key (:meth:`core.config.Settings.validate` and
:func:`configure_encryption` with ``allow_dev_key=False``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from decimal import Decimal
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import String, Text
from sqlalchemy.types import TypeDecorator

from core.logging import get_logger

logger = get_logger("otonom.crypto")

_DEV_SEED = b"otonom-finansal-danisman-dev-only-pii-key"


class _KeyRing:
    fernet: Fernet | None = None
    index_key: bytes = b""
    is_dev: bool = True


_KEYS = _KeyRing()


def _derive_dev_key() -> bytes:
    return base64.urlsafe_b64encode(hashlib.sha256(_DEV_SEED).digest())


class EncryptionKeyError(RuntimeError):
    """Raised when no usable PII key is configured outside dev/test."""


def configure_encryption(key: str | None, *, allow_dev_key: bool = True) -> bool:
    """Install the process-wide PII key.

    Args:
        key: A urlsafe base64 Fernet key, or ``None`` for the dev key.
        allow_dev_key: Whether the derived development key may be used.

    Returns:
        ``True`` when a real (non-dev) key is active.

    Raises:
        EncryptionKeyError: When ``key`` is missing and the dev key is not allowed.
    """
    if not key and not allow_dev_key:
        raise EncryptionKeyError("PII_ENCRYPTION_KEY tanımlı değil (prod ortamında zorunlu).")
    if key:
        raw = key.encode("ascii")
        _KEYS.is_dev = False
    else:
        raw = _derive_dev_key()
        _KEYS.is_dev = True
        logger.warning("pii_dev_key_in_use", hint="PII_ENCRYPTION_KEY tanımlayın (Fernet).")
    _KEYS.fernet = Fernet(raw)
    _KEYS.index_key = hashlib.sha256(b"blind-index:" + raw).digest()
    return not _KEYS.is_dev


def _fernet() -> Fernet:
    if _KEYS.fernet is None:
        configure_encryption(None)
    assert _KEYS.fernet is not None
    return _KEYS.fernet


def encrypt_text(value: str) -> str:
    """Encrypt a string (returns the Fernet token)."""
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_text(token: str) -> str:
    """Decrypt a Fernet token; legacy plain values are returned unchanged."""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return token


def blind_index(value: str) -> str:
    """Keyed, deterministic hash of a normalised value (for lookups/uniqueness)."""
    if _KEYS.fernet is None:
        configure_encryption(None)
    normalised = value.strip().lower().encode("utf-8")
    return hmac.new(_KEYS.index_key, normalised, hashlib.sha256).hexdigest()


class EncryptedString(TypeDecorator[str]):
    """String column stored encrypted at rest."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        if value is None:
            return None
        return encrypt_text(str(value))

    def process_result_value(self, value: Any, dialect: Any) -> str | None:
        if value is None:
            return None
        return decrypt_text(str(value))


class EncryptedDecimal(TypeDecorator[Decimal]):
    """Decimal column stored encrypted at rest (e.g. monthly income)."""

    impl = String(255)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        if value is None:
            return None
        return encrypt_text(str(Decimal(str(value))))

    def process_result_value(self, value: Any, dialect: Any) -> Decimal | None:
        if value is None:
            return None
        try:
            return Decimal(decrypt_text(str(value)))
        except ArithmeticError:
            return Decimal("0")


__all__ = [
    "EncryptionKeyError",
    "EncryptedDecimal",
    "EncryptedString",
    "blind_index",
    "configure_encryption",
    "decrypt_text",
    "encrypt_text",
]
