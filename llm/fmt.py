"""Turkish number formatting helpers shared by templates and the UI API."""

from __future__ import annotations

import math


def _group(int_str: str) -> str:
    out = []
    for i, ch in enumerate(reversed(int_str)):
        if i and i % 3 == 0:
            out.append(".")
        out.append(ch)
    return "".join(reversed(out))


def num(value: float, decimals: int = 1) -> str:
    """Format a number the Turkish way (``1.234,5``)."""
    if value is None or not math.isfinite(float(value)):
        return "-"
    v = float(value)
    sign = "-" if v < 0 else ""
    text = f"{abs(v):.{decimals}f}"
    int_part, _, frac = text.partition(".")
    grouped = _group(int_part)
    return f"{sign}{grouped},{frac}" if decimals > 0 else f"{sign}{grouped}"


def pct(fraction: float, decimals: int = 1) -> str:
    """Format a fraction as a Turkish percentage (``0.142`` → ``%14,2``)."""
    if fraction is None or not math.isfinite(float(fraction)):
        return "-"
    v = float(fraction) * 100.0
    sign = "-" if v < 0 else ""
    return f"{sign}%{num(abs(v), decimals)}"


def tl(amount: float, decimals: int = 0) -> str:
    """Format a TL amount (``1250000`` → ``1.250.000 TL``)."""
    return f"{num(amount, decimals)} TL"


__all__ = ["num", "pct", "tl"]
