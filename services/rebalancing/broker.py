"""Simulated broker: fills approved orders with realistic prices and costs.

* Fill price = reference price × (1 ± slippage) (buys pay up, sells receive
  less), slippage from the policy.
* Fees from :class:`CostModel`; withholding tax on sells from the open tax
  lots (:class:`TaxModel`, FIFO/HIFO).
* Every fill goes through :func:`services.ledger.apply_trade`, so holdings,
  cash, lots and the ledger stay consistent; ``orders``/``fills`` rows are
  written in the same transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.money import to_decimal, to_qty
from core.policy import InvestmentPolicy, get_policy
from models import Fill, Order, Portfolio
from services.ledger import apply_trade
from services.rebalancing.costs import CostModel, TaxModel
from services.tax_lots import open_lots


@dataclass
class ExecutionReport:
    fills: list[dict[str, Any]] = field(default_factory=list)
    total_fees: float = 0.0
    total_tax: float = 0.0
    realized_gain: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "fills": self.fills,
            "total_fees": round(self.total_fees, 2),
            "total_tax": round(self.total_tax, 2),
            "realized_gain": round(self.realized_gain, 2),
        }


class SimulatedBroker:
    """Deterministic execution simulator."""

    def __init__(self, policy: InvestmentPolicy | None = None) -> None:
        self._p = policy or get_policy()
        self._costs = CostModel(self._p)
        self._tax = TaxModel(self._p)

    @property
    def slippage_bps(self) -> float:
        return float(self._p.costs.get("slippage_bps", 0.0))

    async def execute(
        self,
        session: AsyncSession,
        portfolio: Portfolio,
        orders: list[dict[str, Any]],
        prices: dict[str, float],
        *,
        proposal_id: int | None = None,
        reason: str = "rebalance",
    ) -> ExecutionReport:
        """Fill ``orders`` (sells first) inside the caller's transaction.

        Raises:
            services.ledger.LedgerError: When a fill is infeasible.
        """
        report = ExecutionReport()
        slip = self.slippage_bps / 10_000.0
        for spec in sorted(orders, key=lambda o: 0 if o["side"] == "SELL" else 1):
            symbol = str(spec.get("symbol") or spec.get("ticker"))
            side = str(spec["side"])
            ref = float(prices.get(symbol, spec["price"]))
            qty = float(spec["quantity"])
            if side == "SELL":
                held = float((portfolio.holdings or {}).get(symbol, 0.0))
                qty = min(qty, held)
            if qty <= 0:
                continue
            fill_price = ref * (1.0 + slip) if side == "BUY" else ref * (1.0 - slip)
            amount = qty * fill_price
            fees = self._costs.fees(symbol, amount)
            tax = 0.0
            if side == "SELL":
                est = self._tax.estimate_sale(
                    symbol,
                    qty,
                    fill_price,
                    await open_lots(session, portfolio.id, symbol),
                    fees=fees,
                )
                tax = est.tax
                report.realized_gain += est.realized_gain
            if side == "BUY":
                # Kuruş yuvarlamalarında nakit yetmezse miktarı hafifçe azalt.
                affordable = (float(portfolio.cash) - fees) / fill_price
                qty = min(qty, max(affordable, 0.0))
                if qty <= 1e-6:
                    continue
                amount = qty * fill_price
            order = Order(
                proposal_id=proposal_id,
                portfolio_id=portfolio.id,
                symbol=symbol,
                side=side,
                quantity=to_qty(qty),
                reference_price=to_decimal(ref),
                status="FILLED",
            )
            session.add(order)
            await session.flush()
            result = await apply_trade(
                session,
                portfolio,
                symbol=symbol,
                side=side,
                quantity=qty,
                price=fill_price,
                fees=fees,
                tax=tax,
                reason=reason,
                lot_method=self._tax.lot_method,
            )
            await session.flush()
            session.add(
                Fill(
                    order_id=order.id,
                    transaction_id=result.transaction.id,
                    quantity=to_qty(qty),
                    price=to_decimal(fill_price),
                    fees=to_decimal(fees),
                    tax=to_decimal(tax),
                    slippage_bps=self.slippage_bps,
                )
            )
            report.total_fees += fees
            report.total_tax += tax
            report.fills.append(
                {
                    "order_id": order.id,
                    "symbol": symbol,
                    "side": side,
                    "quantity": round(qty, 6),
                    "price": round(fill_price, 6),
                    "amount": round(amount, 2),
                    "fees": round(fees, 4),
                    "tax": round(tax, 4),
                }
            )
        return report


__all__ = ["ExecutionReport", "SimulatedBroker"]
