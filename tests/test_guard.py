"""Unit tests for the number hallucination guard (:mod:`llm.guard`)."""

from __future__ import annotations

import pytest

from llm.fmt import num, pct, tl
from llm.guard import (
    NumberGuardError,
    check_numbers,
    extract_claims,
    parse_number,
    unsupported_claims,
)


@pytest.mark.parametrize(
    ("token", "value", "decimals"),
    [
        ("14,2", 14.2, 1),
        ("14.2", 14.2, 1),
        ("1.250.000", 1_250_000.0, 0),
        ("1.250.000,50", 1_250_000.5, 2),
        ("1,250,000.50", 1_250_000.5, 2),
        ("0.142", 0.142, 3),
        ("−3,5", -3.5, 1),
        ("7", 7.0, 0),
    ],
)
def test_parse_number(token: str, value: float, decimals: int) -> None:
    got, dec = parse_number(token)
    assert got == pytest.approx(value)
    assert dec == decimals


def test_extract_claims_percent_and_amounts() -> None:
    text = "Volatilite %14,2'den 11.8% seviyesine iner; maliyet 1.250 TL, birikim 2,5 milyon TL ve ₺3.400."
    claims = {(c.kind, round(c.value, 3)) for c in extract_claims(text)}
    assert ("percent", 14.2) in claims
    assert ("percent", 11.8) in claims
    assert ("amount", 1250.0) in claims
    assert ("amount", 2_500_000.0) in claims
    assert ("amount", 3400.0) in claims


def test_fraction_context_supports_percent_claim() -> None:
    check_numbers("Volatilite %14,2 seviyesinden %11,8 seviyesine iner.", {"a": 0.142, "b": 0.118})


def test_rounding_tolerance_follows_claim_precision() -> None:
    ctx = {"v": 0.14237}
    check_numbers("yaklaşık %14", ctx)
    check_numbers("tam olarak %14,2", ctx)
    with pytest.raises(NumberGuardError):
        check_numbers("%14,9", ctx)


def test_unsupported_numbers_rejected() -> None:
    bad = unsupported_claims(
        "Portföy %37 getiri ve 99.000 TL kazanç sağlar.", {"x": 0.12, "y": 1500}
    )
    assert len(bad) == 2
    with pytest.raises(NumberGuardError) as err:
        check_numbers("Beklenen getiri %45", {"x": 0.12})
    assert "45" in str(err.value)


def test_plain_numbers_without_units_are_free() -> None:
    check_numbers("Risk seviyesi 6/10, 3 emir ve 2026 yılı.", {})


def test_large_amount_rounding() -> None:
    check_numbers("Medyan birikim 5,4 milyon TL.", {"p50": 5_412_345.0})


def test_formatters_round_trip_through_guard() -> None:
    ctx = {"a": 0.0734, "b": 1234567.891, "c": -0.052}
    text = f"{pct(ctx['a'])} {tl(ctx['b'])} {pct(ctx['c'])}"
    assert text == "%7,3 1.234.568 TL -%5,2"
    check_numbers(text, ctx)
    assert num(1234.5, 2) == "1.234,50"
    assert pct(float("nan")) == "-"
