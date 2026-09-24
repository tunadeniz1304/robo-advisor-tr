"""Multi-asset investment universe (BIST, TL money market & bonds, eurobond,
gold, FX) and its metadata.

Instruments come in two flavours:

    * **direct** — a Yahoo Finance symbol (``XU100.IS``, ``THYAO.IS``,
      ``USDTRY=X``);
    * **proxy** — derived series for products without a free daily history
      (TL money-market fund, TL bond fund, eurobond fund, gram gold in TL),
      computed by :mod:`services.market_data.derive` from underlyings and
      macro data. ``source="proxy"`` makes this explicit in the API/UI.

Risk scores follow an SRRI-like 1–7 scale used by the suitability gate.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InstrumentSpec:
    """Static description of one instrument."""

    symbol: str
    name: str
    asset_class: str
    risk_score: int
    currency: str = "TRY"
    source: str = "yfinance"
    yahoo_symbol: str | None = None
    expense_ratio: float = 0.0
    liquidity_days: int = 1
    min_trade_amount: float = 0.0
    sector: str | None = None
    esg_member: bool = False
    esg_score: float | None = None
    description: str | None = None


def _stock(symbol: str, name: str, sector: str, esg: bool, esg_score: float) -> InstrumentSpec:
    return InstrumentSpec(
        symbol=symbol,
        name=name,
        asset_class="bist_hisse",
        risk_score=7,
        yahoo_symbol=symbol,
        liquidity_days=2,
        min_trade_amount=100.0,
        sector=sector,
        esg_member=esg,
        esg_score=esg_score,
    )


UNIVERSE: tuple[InstrumentSpec, ...] = (
    InstrumentSpec(
        "TL_PPF",
        "TL Para Piyasası Fonu (proxy)",
        "para_piyasasi",
        1,
        source="proxy",
        expense_ratio=0.015,
        description="TCMB politika faizinden yönetim ücreti düşülerek günlük tahakkuk.",
    ),
    InstrumentSpec(
        "TL_TAHVIL",
        "TL Devlet Tahvili Fonu (proxy)",
        "tl_tahvil",
        2,
        source="proxy",
        expense_ratio=0.012,
        description="Faiz getirisi + 2 yıllık süre (duration) ile faiz değişimi etkisi.",
    ),
    InstrumentSpec(
        "EUROBOND_TL",
        "Eurobond Fonu (proxy, TL)",
        "eurobond",
        3,
        source="proxy",
        yahoo_symbol="EMB",
        expense_ratio=0.010,
        description="Gelişen piyasa USD tahvil endeksi (EMB) × USDTRY.",
    ),
    InstrumentSpec(
        "ALTIN_TL",
        "Gram Altın (TL)",
        "altin",
        4,
        source="proxy",
        yahoo_symbol="GC=F",
        description="Ons altın (GC=F) × USDTRY / 31,1035.",
    ),
    InstrumentSpec("USDTRY", "ABD Doları / TL", "doviz", 4, yahoo_symbol="USDTRY=X"),
    InstrumentSpec("EURTRY", "Euro / TL", "doviz", 4, yahoo_symbol="EURTRY=X"),
    InstrumentSpec("XU100.IS", "BIST 100 Endeksi", "bist_endeks", 6, yahoo_symbol="XU100.IS"),
    InstrumentSpec("XU030.IS", "BIST 30 Endeksi", "bist_endeks", 6, yahoo_symbol="XU030.IS"),
    _stock("THYAO.IS", "Türk Hava Yolları", "Ulaştırma", True, 72.0),
    _stock("AKBNK.IS", "Akbank", "Bankacılık", True, 78.0),
    _stock("GARAN.IS", "Garanti BBVA", "Bankacılık", True, 80.0),
    _stock("YKBNK.IS", "Yapı Kredi", "Bankacılık", True, 74.0),
    _stock("ISCTR.IS", "İş Bankası (C)", "Bankacılık", True, 73.0),
    _stock("ASELS.IS", "Aselsan", "Savunma", True, 70.0),
    _stock("SAHOL.IS", "Sabancı Holding", "Holding", True, 76.0),
    _stock("KCHOL.IS", "Koç Holding", "Holding", True, 77.0),
    _stock("BIMAS.IS", "BİM Mağazalar", "Perakende", False, 58.0),
    _stock("EREGL.IS", "Ereğli Demir Çelik", "Metal", True, 66.0),
    _stock("TUPRS.IS", "Tüpraş", "Enerji", True, 64.0),
    _stock("SISE.IS", "Şişecam", "Sanayi", True, 71.0),
    _stock("FROTO.IS", "Ford Otosan", "Otomotiv", True, 75.0),
    _stock("TCELL.IS", "Turkcell", "Telekom", True, 73.0),
    _stock("PGSUS.IS", "Pegasus", "Ulaştırma", False, 60.0),
)

BY_SYMBOL: dict[str, InstrumentSpec] = {spec.symbol: spec for spec in UNIVERSE}

# Canlı indirilen alttaki Yahoo sembolleri (türetilmiş seriler dahil).
UNDERLYING_YAHOO: tuple[str, ...] = (
    "XU100.IS",
    "XU030.IS",
    "USDTRY=X",
    "EURTRY=X",
    "GC=F",
    "EMB",
    *(s.symbol for s in UNIVERSE if s.asset_class == "bist_hisse"),
)

BENCHMARK_SYMBOL = "XU100.IS"

# Her varlık sınıfının model portföyde varsayılan temsilcisi.
CLASS_REPRESENTATIVE: dict[str, str] = {
    "para_piyasasi": "TL_PPF",
    "tl_tahvil": "TL_TAHVIL",
    "eurobond": "EUROBOND_TL",
    "altin": "ALTIN_TL",
    "doviz": "USDTRY",
    "bist_endeks": "XU100.IS",
    "bist_hisse": "THYAO.IS",
}


def asset_class_of(symbol: str) -> str:
    """Asset class of a symbol (unknown symbols are treated as BIST stocks)."""
    spec = BY_SYMBOL.get(symbol)
    if spec is not None:
        return spec.asset_class
    return "bist_hisse" if symbol.endswith(".IS") else "diger"


def symbols_in_class(asset_class: str) -> list[str]:
    return [s.symbol for s in UNIVERSE if s.asset_class == asset_class]


__all__ = [
    "BENCHMARK_SYMBOL",
    "BY_SYMBOL",
    "CLASS_REPRESENTATIVE",
    "InstrumentSpec",
    "UNDERLYING_YAHOO",
    "UNIVERSE",
    "asset_class_of",
    "symbols_in_class",
]
