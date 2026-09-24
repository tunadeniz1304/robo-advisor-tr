"""Transaction cost and withholding-tax models (informational, from policy).

* :class:`CostModel` — commission (bps) + BSMV on commission + half-spread
  + minimum fee, per asset class.
* :class:`TaxModel` — withholding tax on realised gains by asset class and
  holding period; lot selection (FIFO / HIFO) decides which gains realise.
  :func:`harvest_candidates` lists lots with unrealised losses (tax-loss
  harvesting simulation).

All rates are configuration, not code (``config/policy.toml``) and are
informational only — current legislation must be checked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core.policy import InvestmentPolicy, get_policy
from models.base import utcnow
from services.market_data.universe import asset_class_of
from services.tax_lots import order_lots


@dataclass(frozen=True)
class CostBreakdown:
    commission: float
    bsmv: float
    spread: float
    total: float

    def to_dict(self) -> dict[str, float]:
        return {
            "commission": round(self.commission, 4),
            "bsmv": round(self.bsmv, 4),
            "spread": round(self.spread, 4),
            "total": round(self.total, 4),
        }


class CostModel:
    """Per-trade cost estimate."""

    def __init__(self, policy: InvestmentPolicy | None = None) -> None:
        self._p = policy or get_policy()

    def estimate(self, symbol: str, amount: float) -> CostBreakdown:
        """Costs of trading ``amount`` TL (absolute) of ``symbol``."""
        amount = abs(float(amount))
        cls = asset_class_of(symbol)
        commission = amount * self._p.commission_bps(cls) / 10_000.0
        if commission > 0:
            commission = max(commission, float(self._p.costs.get("min_fee", 0.0)))
        bsmv = commission * float(self._p.costs.get("bsmv_rate", 0.0))
        spread = amount * self._p.spread_bps(cls) / 20_000.0  # yarım spread
        return CostBreakdown(commission, bsmv, spread, commission + bsmv + spread)

    def fees(self, symbol: str, amount: float) -> float:
        """Explicit fees charged to cash (commission + BSMV)."""
        c = self.estimate(symbol, amount)
        return c.commission + c.bsmv


@dataclass(frozen=True)
class TaxEstimate:
    tax: float
    realized_gain: float
    lots: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tax": round(self.tax, 4),
            "realized_gain": round(self.realized_gain, 4),
            "lots": self.lots,
        }


class TaxModel:
    """Withholding tax on realised gains (lot based)."""

    def __init__(self, policy: InvestmentPolicy | None = None) -> None:
        self._p = policy or get_policy()

    @property
    def lot_method(self) -> str:
        return str(self._p.rebalance.get("lot_method", "FIFO"))

    def rate(self, symbol: str, acquired_at: datetime, now: datetime | None = None) -> float:
        days = ((now or utcnow()) - acquired_at).days
        return self._p.withholding_rate(asset_class_of(symbol), days)

    def estimate_sale(
        self,
        symbol: str,
        quantity: float,
        price: float,
        lots: list[Any],
        method: str | None = None,
        now: datetime | None = None,
    ) -> TaxEstimate:
        """Tax of selling ``quantity`` at ``price`` consuming ``lots``.

        Losses on one lot offset gains on another within the same sale; the
        tax is never negative. Quantities without lots have zero gain.
        """
        remaining = float(quantity)
        tax = gain_total = 0.0
        used: list[dict[str, Any]] = []
        for lot in order_lots(list(lots), method or self.lot_method):
            if remaining <= 1e-12:
                break
            open_qty = float(lot.quantity_open)
            take = min(remaining, open_qty)
            if take <= 0:
                continue
            gain = (float(price) - float(lot.unit_cost)) * take
            rate = self.rate(symbol, lot.acquired_at, now)
            gain_total += gain
            tax += gain * rate
            used.append(
                {
                    "lot_id": getattr(lot, "id", None),
                    "quantity": round(take, 8),
                    "unit_cost": round(float(lot.unit_cost), 6),
                    "gain": round(gain, 4),
                    "rate": rate,
                }
            )
            remaining -= take
        return TaxEstimate(max(tax, 0.0), gain_total, used)


def harvest_candidates(
    lots: list[Any], prices: dict[str, float], policy: InvestmentPolicy | None = None
) -> list[dict[str, Any]]:
    """Lots with unrealised losses and the tax they could offset (simulation)."""
    policy = policy or get_policy()
    out: list[dict[str, Any]] = []
    now = utcnow()
    for lot in lots:
        price = prices.get(lot.symbol)
        if price is None:
            continue
        loss = (float(price) - float(lot.unit_cost)) * float(lot.quantity_open)
        if loss >= 0:
            continue
        rate = policy.withholding_rate(asset_class_of(lot.symbol), (now - lot.acquired_at).days)
        out.append(
            {
                "lot_id": lot.id,
                "symbol": lot.symbol,
                "quantity": float(lot.quantity_open),
                "unit_cost": float(lot.unit_cost),
                "price": float(price),
                "unrealized_loss": round(loss, 2),
                "withholding_rate": rate,
                "potential_tax_offset": round(-loss * rate, 2),
            }
        )
    out.sort(key=lambda r: r["unrealized_loss"])
    return out


__all__ = ["CostBreakdown", "CostModel", "TaxEstimate", "TaxModel", "harvest_candidates"]
