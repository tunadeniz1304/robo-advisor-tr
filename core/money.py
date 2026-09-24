"""Money helpers: exact ``Decimal`` arithmetic and a portable SQL type.

* All monetary amounts are persisted as ``NUMERIC(20, 6)`` in PostgreSQL and
  as exact decimal strings in SQLite (which has no native decimal type), via
  :class:`Money`.
* Services compute in ``float`` (NumPy) and convert at the persistence
  boundary with :func:`to_decimal`; API responses use :func:`to_float`.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Numeric, String
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator, TypeEngine

MONEY_SCALE = 6
QTY_SCALE = 8
_MONEY_Q = Decimal(1).scaleb(-MONEY_SCALE)
_QTY_Q = Decimal(1).scaleb(-QTY_SCALE)
_CENT = Decimal("0.01")

DEFAULT_CURRENCY = "TRY"
SUPPORTED_CURRENCIES = ("TRY", "USD", "EUR")


def to_decimal(value: Any, scale: int = MONEY_SCALE) -> Decimal:
    """Convert any numeric-ish value to a quantized ``Decimal``.

    Floats are converted through ``repr`` to avoid binary artefacts
    (``0.1`` → ``Decimal('0.1')``).

    Raises:
        ValueError: If the value is not numeric.
    """
    if isinstance(value, Decimal):
        dec = value
    elif isinstance(value, float):
        dec = Decimal(repr(value))
    else:
        try:
            dec = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"Geçersiz tutar: {value!r}") from exc
    if not dec.is_finite():
        raise ValueError(f"Geçersiz tutar: {value!r}")
    q = Decimal(1).scaleb(-scale)
    return dec.quantize(q, rounding=ROUND_HALF_EVEN)


def to_qty(value: Any) -> Decimal:
    """Quantize a quantity (8 decimals)."""
    return to_decimal(value, QTY_SCALE)


def round_cents(value: Any) -> Decimal:
    """Round to 2 decimals (display / settlement)."""
    return to_decimal(value).quantize(_CENT, rounding=ROUND_HALF_UP)


def to_float(value: Any, digits: int = 2) -> float:
    """Convert a money value to a rounded float for JSON responses."""
    if value is None:
        return 0.0
    return round(float(value), digits)


class Money(TypeDecorator[Decimal]):
    """Portable exact decimal column (``NUMERIC(20,6)`` / SQLite text)."""

    impl = Numeric(20, MONEY_SCALE)
    cache_ok = True

    def __init__(self, scale: int = MONEY_SCALE, **_ignored: Any) -> None:
        # Alembic, tipi ``Money(precision=20, scale=6)`` olarak render eder;
        # yalnızca ``scale`` anlamlıdır, diğer argümanlar yok sayılır.
        super().__init__()
        self.scale = scale

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(40))
        return dialect.type_descriptor(Numeric(20 + (self.scale - MONEY_SCALE), self.scale))

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        dec = to_decimal(value, self.scale)
        return str(dec) if dialect.name == "sqlite" else dec

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        return to_decimal(value, self.scale)


class Quantity(Money):
    """Exact quantity column with 8 decimals."""

    def __init__(self, **_ignored: Any) -> None:
        super().__init__(scale=QTY_SCALE)


__all__ = [
    "DEFAULT_CURRENCY",
    "MONEY_SCALE",
    "Money",
    "QTY_SCALE",
    "Quantity",
    "SUPPORTED_CURRENCIES",
    "round_cents",
    "to_decimal",
    "to_float",
    "to_qty",
]
