"""Lot-based tax engine (informational): cost basis, realised P&L and harvesting.

The engine replays the transaction ledger through a :class:`LotBook`:

* every buy opens a lot whose unit cost includes its commission;
* every sell consumes lots in ``FIFO`` (oldest first) or ``HIFO`` (highest
  cost first) order; the realised gain of each consumed slice is
  ``(sale price − unit cost) × qty − the slice's share of the sale costs``;
* the withholding rate comes from the policy table by asset class and
  holding period (``[tax]`` in ``config/policy.toml``); within one sale,
  losses offset gains of the same asset class and the tax is never negative.

Tax-loss harvesting is simulated conservatively: an unrealised loss only
"saves" tax up to the gains already realised this year in the **same asset
class** (withholding in Turkey is levied per product; cross-product offset
is limited). Everything here is informational — rates are examples from the
policy file, not tax advice.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from core.policy import InvestmentPolicy, get_policy
from services.market_data.universe import asset_class_of

LOT_METHODS = ("FIFO", "HIFO")
EPS = 1e-9


@dataclass
class Lot:
    symbol: str
    quantity: float
    unit_cost: float
    acquired: date
    lot_id: int = 0


@dataclass(frozen=True)
class RealizedSlice:
    """One consumed lot slice of a sale."""

    symbol: str
    asset_class: str
    quantity: float
    unit_cost: float
    sale_price: float
    acquired: date
    sold: date
    gain: float
    holding_days: int
    withholding_rate: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "asset_class": self.asset_class,
            "quantity": round(self.quantity, 8),
            "unit_cost": round(self.unit_cost, 6),
            "sale_price": round(self.sale_price, 6),
            "acquired": self.acquired.isoformat(),
            "sold": self.sold.isoformat(),
            "holding_days": self.holding_days,
            "gain": round(self.gain, 2),
            "withholding_rate": self.withholding_rate,
        }


@dataclass
class SaleResult:
    slices: list[RealizedSlice] = field(default_factory=list)
    untracked_quantity: float = 0.0  # lotu olmayan (takip öncesi) miktar: kazanç 0

    @property
    def gain(self) -> float:
        return sum(s.gain for s in self.slices)

    def tax(self) -> float:
        """Withholding of this sale: per class, losses offset gains, never negative."""
        by_class: dict[str, float] = defaultdict(float)
        for s in self.slices:
            by_class[s.asset_class] += s.gain * s.withholding_rate
        return sum(max(0.0, v) for v in by_class.values())


class LotBook:
    """Open lots per symbol with FIFO/HIFO consumption."""

    def __init__(self, method: str = "FIFO", policy: InvestmentPolicy | None = None) -> None:
        method = method.upper()
        if method not in LOT_METHODS:
            raise ValueError(f"Bilinmeyen lot yöntemi: {method}")
        self.method = method
        self._p = policy or get_policy()
        self._lots: dict[str, list[Lot]] = defaultdict(list)
        self._next_id = 1

    def buy(self, symbol: str, quantity: float, price: float, fees: float, when: date) -> Lot:
        if quantity <= 0 or price <= 0:
            raise ValueError("Miktar ve fiyat pozitif olmalı.")
        lot = Lot(symbol, float(quantity), float(price) + float(fees) / float(quantity), when)
        lot.lot_id = self._next_id
        self._next_id += 1
        self._lots[symbol].append(lot)
        return lot

    def _ordered(self, symbol: str) -> list[Lot]:
        lots = [lot for lot in self._lots[symbol] if lot.quantity > EPS]
        if self.method == "HIFO":
            return sorted(lots, key=lambda lot: (-lot.unit_cost, lot.acquired, lot.lot_id))
        return sorted(lots, key=lambda lot: (lot.acquired, lot.lot_id))

    def sell(
        self,
        symbol: str,
        quantity: float,
        price: float,
        costs: float,
        when: date,
        *,
        dry_run: bool = False,
    ) -> SaleResult:
        """Consume lots for a sale; ``dry_run`` leaves the book unchanged."""
        remaining = float(quantity)
        result = SaleResult()
        cost_per_unit = float(costs) / float(quantity) if quantity > 0 else 0.0
        cls = asset_class_of(symbol)
        for lot in self._ordered(symbol):
            if remaining <= EPS:
                break
            take = min(remaining, lot.quantity)
            days = (when - lot.acquired).days
            rate = self._p.withholding_rate(cls, days)
            gain = (float(price) - lot.unit_cost - cost_per_unit) * take
            result.slices.append(
                RealizedSlice(
                    symbol,
                    cls,
                    take,
                    lot.unit_cost,
                    float(price),
                    lot.acquired,
                    when,
                    gain,
                    days,
                    rate,
                )
            )
            if not dry_run:
                lot.quantity -= take
            remaining -= take
        result.untracked_quantity = max(0.0, remaining)
        return result

    def open_lots(self) -> list[Lot]:
        return [lot for lots in self._lots.values() for lot in lots if lot.quantity > EPS]


def _day(value: datetime | date) -> date:
    return value.date() if isinstance(value, datetime) else value


def replay(
    trades: list[dict[str, Any]], method: str, policy: InvestmentPolicy | None = None
) -> tuple[LotBook, list[RealizedSlice]]:
    """Replay ledger trades (``symbol, side, quantity, price, fees, tax, when``)."""
    book = LotBook(method, policy)
    realized: list[RealizedSlice] = []
    for t in sorted(trades, key=lambda x: (x["when"], x.get("id", 0))):
        when = _day(t["when"])
        if t["side"] == "BUY":
            book.buy(t["symbol"], t["quantity"], t["price"], t.get("fees", 0.0), when)
        else:
            res = book.sell(t["symbol"], t["quantity"], t["price"], t.get("fees", 0.0), when)
            realized.extend(res.slices)
    return book, realized


def realized_summary(realized: list[RealizedSlice], year: int | None = None) -> dict[str, Any]:
    rows = [r for r in realized if year is None or r.sold.year == year]
    by_class: dict[str, dict[str, float]] = {}
    for r in rows:
        c = by_class.setdefault(r.asset_class, {"gain": 0.0, "tax": 0.0})
        c["gain"] += r.gain
        c["tax"] += r.gain * r.withholding_rate
    for c in by_class.values():
        c["tax"] = max(0.0, c["tax"])
        c["gain"] = round(c["gain"], 2)
        c["tax"] = round(c["tax"], 2)
    return {
        "year": year,
        "realized": [r.to_dict() for r in rows],
        "by_class": by_class,
        "total_gain": round(sum(r.gain for r in rows), 2),
        "estimated_tax": round(sum(c["tax"] for c in by_class.values()), 2),
    }


def harvest_simulation(
    book: LotBook,
    prices: dict[str, float],
    realized: list[RealizedSlice],
    *,
    today: date,
    policy: InvestmentPolicy | None = None,
) -> dict[str, Any]:
    """Lots with unrealised losses and the tax they could save this year.

    The saving of a class is capped by the gains already realised this year in
    that class: ``saving_c = min(loss_c, gain_c) × rate``.
    """
    policy = policy or get_policy()
    gains: dict[str, float] = defaultdict(float)
    for r in realized:
        if r.sold.year == today.year:
            gains[r.asset_class] += r.gain
    candidates: list[dict[str, Any]] = []
    loss_by_class: dict[str, float] = defaultdict(float)
    for lot in book.open_lots():
        price = prices.get(lot.symbol)
        if price is None:
            continue
        loss = (float(price) - lot.unit_cost) * lot.quantity
        if loss >= 0:
            continue
        cls = asset_class_of(lot.symbol)
        rate = policy.withholding_rate(cls, (today - lot.acquired).days)
        loss_by_class[cls] += -loss
        candidates.append(
            {
                "symbol": lot.symbol,
                "asset_class": cls,
                "lot_id": lot.lot_id,
                "quantity": round(lot.quantity, 8),
                "unit_cost": round(lot.unit_cost, 6),
                "price": float(price),
                "unrealized_loss": round(loss, 2),
                "withholding_rate": rate,
            }
        )
    savings = {}
    for cls, loss in loss_by_class.items():
        rate = max(
            (c["withholding_rate"] for c in candidates if c["asset_class"] == cls), default=0
        )
        usable = min(loss, max(0.0, gains.get(cls, 0.0)))
        savings[cls] = {
            "unrealized_loss": round(-loss, 2),  # adaylarla aynı işaret: zarar negatif
            "realized_gain_ytd": round(gains.get(cls, 0.0), 2),
            "offsettable": round(usable, 2),
            "estimated_saving": round(usable * rate, 2),
        }
    candidates.sort(key=lambda c: c["unrealized_loss"])
    return {
        "candidates": candidates,
        "by_class": savings,
        "total_estimated_saving": round(sum(v["estimated_saving"] for v in savings.values()), 2),
        "note": "Bilgi amaçlıdır: stopaj ürün bazında kesilir, zarar mahsubu sınırlıdır; oranlar "
        "politika dosyasındaki örnek tablodandır. Aynı enstrümanı hemen geri almak ekonomik "
        "olarak pozisyonu değiştirmez.",
    }


__all__ = [
    "LOT_METHODS",
    "Lot",
    "LotBook",
    "RealizedSlice",
    "SaleResult",
    "harvest_simulation",
    "realized_summary",
    "replay",
]
