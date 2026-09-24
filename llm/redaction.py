"""KVKK redaction: no personal data leaves the platform in an LLM prompt.

Every context sent to an LLM passes through :func:`redact`:

    * direct identifiers (name, e-mail, phone, TCKN, address, username) are
      dropped;
    * ``customer_id`` becomes a stable pseudonym (``MUSTERI_12``);
    * income fields are replaced by a coarse band (``gelir_bandi: "30-50k"``);
    * free-text strings are scrubbed of e-mail addresses, phone numbers and
      11 digit TCKN-like numbers.

The transformation is deterministic and pure, so tests can assert on it.
"""

from __future__ import annotations

import re
from typing import Any

# Tamamen düşürülen anahtarlar (küçük harf karşılaştırma).
DROP_KEYS: frozenset[str] = frozenset(
    {
        "full_name",
        "name",
        "ad",
        "ad_soyad",
        "customer_name",
        "email",
        "e_posta",
        "eposta",
        "phone",
        "telefon",
        "tckn",
        "tc_kimlik",
        "address",
        "adres",
        "username",
        "kullanici_adi",
        "password",
        "password_hash",
    }
)
INCOME_KEYS: frozenset[str] = frozenset(
    {"monthly_income", "income", "gelir", "aylik_gelir", "net_gelir"}
)
ID_KEYS: frozenset[str] = frozenset({"customer_id", "musteri_id"})

# Gelir bantları (TL/ay, üst sınır hariç) → etiket.
INCOME_BANDS: tuple[tuple[float, str], ...] = (
    (15_000, "0-15k"),
    (30_000, "15-30k"),
    (50_000, "30-50k"),
    (100_000, "50-100k"),
    (250_000, "100-250k"),
    (float("inf"), "250k+"),
)

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?:\+?90[\s-]?)?\(?0?5\d{2}\)?[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}")
_TCKN = re.compile(r"\b[1-9]\d{10}\b")


def pseudonym(customer_id: int | str) -> str:
    """Stable pseudonym for a customer id (``MUSTERI_12``)."""
    return f"MUSTERI_{customer_id}"


def income_band(value: float | int | str | None) -> str:
    """Map a monthly income to a coarse band label."""
    try:
        income = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "bilinmiyor"
    for upper, label in INCOME_BANDS:
        if income < upper:
            return label
    return INCOME_BANDS[-1][1]


def scrub_text(text: str, names: tuple[str, ...] = ()) -> str:
    """Remove e-mails, phone numbers, TCKN-like numbers and known names."""
    out = _EMAIL.sub("[EPOSTA]", text)
    out = _PHONE.sub("[TELEFON]", out)
    out = _TCKN.sub("[KIMLIK]", out)
    for name in names:
        if name and len(name) >= 2:
            out = re.sub(re.escape(name), "[AD]", out, flags=re.IGNORECASE)
    return out


def redact(obj: Any, names: tuple[str, ...] = ()) -> Any:
    """Return a redacted deep copy of a JSON-like structure.

    Args:
        obj: Context dict/list/scalar about to be sent to an LLM.
        names: Extra personal names to scrub from free text.

    Returns:
        The redacted structure (input is not mutated).
    """
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            k = str(key).lower()
            if k in DROP_KEYS:
                continue
            if k in INCOME_KEYS:
                out["gelir_bandi"] = income_band(value)
                continue
            if k in ID_KEYS and isinstance(value, int | str):
                out["musteri"] = pseudonym(value)
                continue
            out[key] = redact(value, names)
        return out
    if isinstance(obj, list):
        return [redact(v, names) for v in obj]
    if isinstance(obj, tuple):
        return [redact(v, names) for v in obj]
    if isinstance(obj, str):
        return scrub_text(obj, names)
    return obj


__all__ = ["income_band", "pseudonym", "redact", "scrub_text"]
