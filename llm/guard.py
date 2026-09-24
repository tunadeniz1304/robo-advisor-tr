"""Number hallucination guard for LLM output.

The LLM is allowed to *explain* numbers, never to invent them. Every
percentage or monetary amount found in a model answer must match a number in
the context JSON that was sent to the model (within a rounding tolerance).

Recognised claims (Turkish and English formatting):
    * percentages: ``%14,2``, ``% 3``, ``14.2%``, ``-2,5 %``
    * amounts:     ``1.250.000 TL``, ``12,5 bin TL``, ``₺3.400``, ``$1,200``,
      ``2,1 milyon TL``, ``350 USD``

A context number ``c`` supports a claim ``x`` when ``x ≈ c`` or ``x ≈ 100·c``
(fractions rendered as percentages). The tolerance follows the precision of
the claim: ``%14`` accepts 13.5–14.5, ``%14,2`` accepts 14.15–14.25, plus a
0.5 % relative slack for rounding of large amounts.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

_NUMBER = r"[-+−]?\d+(?:[.,]\d+)*"
_PCT_PREFIX = re.compile(rf"%\s?({_NUMBER})")
_PCT_SUFFIX = re.compile(rf"({_NUMBER})\s?%")
_MULT = r"(?:\s?(bin|milyon|milyar))?"
_CURRENCY_SUFFIX = re.compile(
    rf"({_NUMBER}){_MULT}\s?(?:TL|₺|TRY|USD|EUR|\$|€|lira)\b", re.IGNORECASE
)
_CURRENCY_PREFIX = re.compile(rf"(?:₺|\$|€)\s?({_NUMBER}){_MULT}", re.IGNORECASE)
_ANY_NUMBER = re.compile(_NUMBER)

_MULTIPLIERS = {"bin": 1e3, "milyon": 1e6, "milyar": 1e9}


class NumberGuardError(ValueError):
    """Raised when the output contains numbers absent from the context."""

    def __init__(self, unsupported: list[str]) -> None:
        super().__init__("Bağlamda olmayan sayılar: " + ", ".join(unsupported[:5]))
        self.unsupported = unsupported


@dataclass(frozen=True)
class Claim:
    """A numeric claim extracted from text."""

    raw: str
    value: float
    decimals: int
    kind: str  # "percent" | "amount"


def parse_number(token: str) -> tuple[float, int]:
    """Parse a Turkish/English formatted number.

    Rules: with both ``.`` and ``,`` the last separator is the decimal mark;
    ``1.250.000`` (groups of three) is thousands-grouped; a single ``,`` is a
    decimal comma; a single ``.`` followed by exactly three digits is treated
    as a thousands separator only when more groups exist.

    Args:
        token: The numeric token (may include a sign).

    Returns:
        ``(value, decimals)`` where ``decimals`` is the number of fractional
        digits written in the token.
    """
    tok = token.replace("−", "-").replace("+", "")
    sign = -1.0 if tok.startswith("-") else 1.0
    tok = tok.lstrip("-")
    if "," in tok and "." in tok:
        dec_sep = "," if tok.rfind(",") > tok.rfind(".") else "."
        thou_sep = "." if dec_sep == "," else ","
        tok = tok.replace(thou_sep, "")
        int_part, _, frac = tok.partition(dec_sep)
    elif "," in tok:
        parts = tok.split(",")
        if len(parts) > 2 and all(len(p) == 3 for p in parts[1:]):
            int_part, frac = "".join(parts), ""
        else:
            int_part, _, frac = tok.partition(",")
    elif "." in tok:
        parts = tok.split(".")
        if len(parts) > 2 and all(len(p) == 3 for p in parts[1:]):
            int_part, frac = "".join(parts), ""
        elif len(parts) == 2 and len(parts[1]) == 3 and len(parts[0]) <= 3 and parts[0] != "0":
            # "1.250" → Türkçe binlik ayırıcı varsayımı
            int_part, frac = "".join(parts), ""
        else:
            int_part, _, frac = tok.partition(".")
    else:
        int_part, frac = tok, ""
    value = float(f"{int_part or 0}.{frac or 0}")
    return sign * value, len(frac)


def extract_claims(text: str) -> list[Claim]:
    """Extract percentage and amount claims from free text."""
    claims: list[Claim] = []
    seen_spans: list[tuple[int, int]] = []

    def _add(match: re.Match[str], kind: str, mult_group: int | None = None) -> None:
        span = match.span(1)
        if any(s <= span[0] < e for s, e in seen_spans):
            return
        value, decimals = parse_number(match.group(1))
        if mult_group is not None and match.group(mult_group):
            value *= _MULTIPLIERS[match.group(mult_group).lower()]
            decimals = 0 if decimals == 0 else decimals
        seen_spans.append(span)
        claims.append(Claim(raw=match.group(0).strip(), value=value, decimals=decimals, kind=kind))

    for m in _PCT_PREFIX.finditer(text):
        _add(m, "percent")
    for m in _PCT_SUFFIX.finditer(text):
        _add(m, "percent")
    for m in _CURRENCY_SUFFIX.finditer(text):
        _add(m, "amount", 2)
    for m in _CURRENCY_PREFIX.finditer(text):
        _add(m, "amount", 2)
    return claims


def collect_numbers(obj: Any) -> list[float]:
    """Flatten every numeric leaf (and numbers inside strings) of a JSON value."""
    out: list[float] = []

    def _walk(value: Any) -> None:
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, int | float):
            if math.isfinite(float(value)):
                out.append(float(value))
        elif isinstance(value, str):
            for claim in extract_claims(value):
                out.append(claim.value)
            for tok in _ANY_NUMBER.findall(value):
                try:
                    out.append(parse_number(tok)[0])
                except ValueError:
                    continue
        elif isinstance(value, dict):
            for v in value.values():
                _walk(v)
        elif isinstance(value, list | tuple | set):
            for v in value:
                _walk(v)
        else:
            try:
                f = float(value)
            except (TypeError, ValueError):
                return
            if math.isfinite(f):
                out.append(f)

    _walk(obj)
    return out


def _supported(claim: Claim, allowed: Iterable[float]) -> bool:
    step = 10.0 ** (-claim.decimals) if claim.decimals > 0 else 1.0
    abs_tol = 0.5 * step + 1e-9
    if claim.kind == "amount" and claim.value >= 1000 and claim.decimals == 0:
        # "12,5 bin TL" gibi yuvarlamalar için basamak toleransı
        magnitude = 10 ** max(0, int(math.log10(abs(claim.value))) - 2)
        abs_tol = max(abs_tol, 0.5 * magnitude)
    x = claim.value
    for c in allowed:
        for candidate in (c, c * 100.0):
            tol = max(abs_tol, abs(candidate) * 0.005)
            if abs(abs(x) - abs(candidate)) <= tol:
                return True
    return False


def unsupported_claims(text: str, context: Any, extra_allowed: Iterable[float] = ()) -> list[str]:
    """Return the raw claims in ``text`` that the context does not support."""
    allowed = collect_numbers(context) + [float(v) for v in extra_allowed]
    # 0 ve 100 her zaman serbest (ör. "%100 hisse", "0 TL").
    allowed += [0.0, 100.0]
    return [c.raw for c in extract_claims(text) if not _supported(c, allowed)]


def check_numbers(text: str, context: Any, extra_allowed: Iterable[float] = ()) -> None:
    """Raise :class:`NumberGuardError` when ``text`` has unsupported numbers."""
    bad = unsupported_claims(text, context, extra_allowed)
    if bad:
        raise NumberGuardError(bad)


def texts_of(obj: Any) -> list[str]:
    """Collect every string leaf of a (pydantic-dumped) structure."""
    out: list[str] = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(texts_of(v))
    elif isinstance(obj, list | tuple):
        for v in obj:
            out.extend(texts_of(v))
    return out


__all__ = [
    "Claim",
    "NumberGuardError",
    "check_numbers",
    "collect_numbers",
    "extract_claims",
    "parse_number",
    "texts_of",
    "unsupported_claims",
]
