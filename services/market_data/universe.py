"""Multi-asset investment universe (BIST, TL money market & bonds, eurobond,
gold, FX) and its metadata.

Every instrument is backed by real data (see ``docs/DATA.md``):

    * ``yfinance`` — BIST index/stocks and FX from Yahoo Finance;
    * ``derived``  — gram gold in TL = ounce gold (GC=F) × USDTRY / 31.1035,
      a unit conversion of two real market series;
    * ``tefas``    — daily NAV of a real TEFAS fund (``tefas_code``). TEFAS
      serves only the last five years; for class representatives the older
      part is spliced from a documented proxy (``splice``) and the snapshot
      metadata records the date until which the series is a proxy.

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
    tefas_code: str | None = None
    splice: str | None = None  # TEFAS öncesi dönem için vekil yöntemi (yoksa kısa seri)
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
        "İş Portföy Para Piyasası (TL) Fonu — TI1",
        "para_piyasasi",
        1,
        source="tefas",
        tefas_code="TI1",
        splice="money_market",
        expense_ratio=0.015,
        description="Gerçek TEFAS fiyatı (TI1). 5 yıldan eski kısım politika faizi tahakkukuyla "
        "eklenmiş vekildir (meta: proxy_until).",
    ),
    InstrumentSpec(
        "TL_TAHVIL",
        "Ziraat Portföy Kısa Vadeli Borçlanma Araçları (TL) Fonu — TZV",
        "tl_tahvil",
        2,
        source="tefas",
        tefas_code="TZV",
        splice="bond",
        expense_ratio=0.012,
        description="Gerçek TEFAS fiyatı (TZV). 5 yıldan eski kısım faiz + süre modeliyle vekildir.",
    ),
    InstrumentSpec(
        "YOT",
        "Yapı Kredi Portföy Borçlanma Araçları Fonu — YOT",
        "tl_tahvil",
        3,
        source="tefas",
        tefas_code="YOT",
        expense_ratio=0.015,
        description="Gerçek TEFAS fiyatı; daha uzun vadeli TL borçlanma araçları (yalnız son 5 yıl).",
    ),
    InstrumentSpec(
        "EUROBOND_TL",
        "Yapı Kredi Portföy Eurobond (Dolar) Borçlanma Araçları Fonu — YBE",
        "eurobond",
        3,
        source="tefas",
        tefas_code="YBE",
        splice="eurobond",
        yahoo_symbol="EMB",
        expense_ratio=0.010,
        description="Gerçek TEFAS fiyatı (YBE). 5 yıldan eski kısım EMB × USDTRY vekilidir.",
    ),
    InstrumentSpec(
        "IPV",
        "İş Portföy Eurobond Borçlanma Araçları (Döviz) Fonu — IPV",
        "eurobond",
        3,
        source="tefas",
        tefas_code="IPV",
        expense_ratio=0.012,
        description="Gerçek TEFAS fiyatı (yalnız son 5 yıl).",
    ),
    InstrumentSpec(
        "ALTIN_TL",
        "Gram Altın (TL)",
        "altin",
        4,
        source="derived",
        yahoo_symbol="GC=F",
        description="Ons altın (GC=F) × USDTRY / 31,1035 — iki gerçek piyasa serisinin birim dönüşümü.",
    ),
    InstrumentSpec(
        "TTA",
        "İş Portföy Altın Fonu — TTA",
        "altin",
        4,
        source="tefas",
        tefas_code="TTA",
        expense_ratio=0.018,
        description="Gerçek TEFAS fiyatı (yalnız son 5 yıl).",
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


TEFAS_FUNDS: dict[str, str] = {s.symbol: s.tefas_code for s in UNIVERSE if s.tefas_code}


def symbols_in_class(asset_class: str) -> list[str]:
    return [s.symbol for s in UNIVERSE if s.asset_class == asset_class]


__all__ = [
    "BENCHMARK_SYMBOL",
    "BY_SYMBOL",
    "CLASS_REPRESENTATIVE",
    "InstrumentSpec",
    "TEFAS_FUNDS",
    "UNDERLYING_YAHOO",
    "UNIVERSE",
    "asset_class_of",
    "symbols_in_class",
]
