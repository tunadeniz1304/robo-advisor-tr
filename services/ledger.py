"""Ledger service — the single write path for trades.

``apply_trade`` validates a trade against the portfolio and, inside the
caller's transaction, atomically:

    * updates ``portfolios.holdings`` and ``portfolios.cash`` (exact Decimal),
    * inserts the immutable ``transactions`` row,
    * maintains tax lots (FIFO/HIFO consumption on sells) when enabled.

Manual trades, simulated broker fills and cash sweeps all use it, so the
ledger, positions and cash can never diverge (bug #6).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.logging import get_logger
from core.money import to_decimal, to_qty
from models import Portfolio, Transaction

logger = get_logger("otonom.ledger")

CASH_TOLERANCE = Decimal("0.01")


class LedgerError(ValueError):
    """Raised when a trade is not feasible (insufficient cash/quantity)."""


@dataclass(frozen=True)
class TradeResult:
    """Outcome of an applied trade."""

    transaction: Transaction
    cash_after: Decimal
    quantity_after: Decimal
    realized_pnl: Decimal
    lots_consumed: list[dict[str, Any]]


async def apply_trade(
    session: AsyncSession,
    portfolio: Portfolio,
    *,
    symbol: str,
    side: str,
    quantity: Any,
    price: Any,
    fees: Any = 0,
    tax: Any = 0,
    reason: str = "manual",
    currency: str | None = None,
    executed_at: datetime | None = None,
    lot_method: str = "FIFO",
    track_lots: bool = True,
) -> TradeResult:
    """Apply one trade to a portfolio (no commit; caller owns the transaction).

    Args:
        session: Active session (the caller commits).
        portfolio: Loaded portfolio row (mutated in place).
        symbol: Instrument symbol.
        side: ``BUY`` or ``SELL``.
        quantity: Positive quantity.
        price: Positive execution price.
        fees: Commission + BSMV + other costs (reduce cash).
        tax: Withholding tax on the sale (reduces cash).
        reason: Ledger reason (``manual``, ``rebalance``, ``sweep`` …).
        currency: Trade currency (defaults to the portfolio currency).
        executed_at: Execution timestamp (defaults to now).
        lot_method: ``FIFO`` or ``HIFO`` for sell lot selection.
        track_lots: Maintain the tax-lot table.

    Returns:
        :class:`TradeResult`.

    Raises:
        LedgerError: When cash or quantity is insufficient or inputs invalid.
    """
    side = side.upper()
    if side not in {"BUY", "SELL"}:
        raise LedgerError("İşlem yönü BUY veya SELL olmalıdır.")
    qty = to_qty(quantity)
    px = to_decimal(price)
    fee = to_decimal(fees)
    tx_tax = to_decimal(tax)
    if qty <= 0 or px <= 0:
        raise LedgerError("Miktar ve fiyat pozitif olmalıdır.")
    if fee < 0 or tx_tax < 0:
        raise LedgerError("Maliyet ve vergi negatif olamaz.")

    gross = to_decimal(qty * px)
    holdings = {k: Decimal(str(v)) for k, v in (portfolio.holdings or {}).items()}
    held = holdings.get(symbol, Decimal("0"))
    cash = to_decimal(portfolio.cash)

    if side == "BUY":
        needed = gross + fee + tx_tax
        if needed > cash + CASH_TOLERANCE:
            raise LedgerError(
                f"Yetersiz nakit: gereken {needed:.2f}, mevcut {cash:.2f} {portfolio.currency}."
            )
        cash = max(cash - needed, Decimal("0"))
        held = held + qty
    else:
        if qty > held + Decimal("0.00000001"):
            raise LedgerError(
                f"Yetersiz miktar: {symbol} için {held} adet mevcut, {qty} satılamaz."
            )
        cash = cash + gross - fee - tx_tax
        held = max(held - qty, Decimal("0"))

    if held > 0:
        holdings[symbol] = held
    else:
        holdings.pop(symbol, None)

    portfolio.holdings = {k: float(v) for k, v in holdings.items()}
    portfolio.cash = to_decimal(cash)

    tx = Transaction(
        portfolio_id=portfolio.id,
        ticker=symbol,
        side=side,
        quantity=qty,
        price=px,
        total_amount=gross,
        fees=fee,
        tax=tx_tax,
        currency=currency or portfolio.currency,
        reason=reason,
    )
    if executed_at is not None:
        tx.executed_at = executed_at
    session.add(tx)

    realized = Decimal("0")
    consumed: list[dict[str, Any]] = []
    if track_lots:
        from services.tax_lots import add_lot, consume_lots

        if side == "BUY":
            await add_lot(session, portfolio.id, symbol, qty, px, fee, executed_at)
        else:
            realized, consumed = await consume_lots(
                session, portfolio.id, symbol, qty, px, method=lot_method
            )

    logger.info(
        "trade_applied",
        portfolio_id=portfolio.id,
        symbol=symbol,
        side=side,
        quantity=str(qty),
        price=str(px),
        reason=reason,
    )
    return TradeResult(
        transaction=tx,
        cash_after=portfolio.cash,
        quantity_after=held,
        realized_pnl=realized,
        lots_consumed=consumed,
    )


__all__ = ["LedgerError", "TradeResult", "apply_trade"]
