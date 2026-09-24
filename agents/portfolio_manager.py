"""Portföy Yöneticisi (Portfolio Manager) — LangGraph node.

Combines the analytic pillars of the system:

    1. **Markowitz MPT** (:class:`services.portfolio_service.PortfolioService`)
       computes target weights from real returns data against a real
       risk-free rate.
    2. **Rebalance through the ledger** — orders are derived once from the
       target weights and applied through :func:`services.ledger.apply_trade`
       inside one DB transaction (sells first, then buys).
    3. **Grounded LLM rationale** via :class:`llm.gateway.LLMGateway`, which
       never raises: provider failures fall back to the deterministic demo
       output. The rebalance therefore runs exactly once even when the LLM is
       down (bug #1 — previously weights and orders were recomputed and a
       second, near-empty rebalance was reported).

The binding weights always come from the optimiser; the LLM only explains.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pandas as pd

from agents.state import AdvisorState
from core.database import session_factory
from core.logging import get_logger
from llm.gateway import GenerationResult, LLMGateway
from llm.prompts import DISCLAIMER
from llm.schemas import RebalanceRationale
from models import AdvisorRun, Portfolio
from services.ledger import apply_trade
from services.portfolio_service import PortfolioService

logger = get_logger("otonom.agent.portfolio")

# Sürtünme maliyeti eşiği: bu tutarın altındaki emirler üretilmez.
MIN_ORDER_AMOUNT = 10.0
MIN_ORDER_QTY = 0.01


def build_orders(
    weights: dict[str, float],
    prices: dict[str, float],
    holdings: dict[str, float],
    cash: float,
) -> list[dict[str, Any]]:
    """Derive rebalancing orders from target weights (pure function).

    Args:
        weights: Target weights (risky assets; the remainder stays in cash).
        prices: Last prices per symbol.
        holdings: Current quantities.
        cash: Current cash.

    Returns:
        Orders ``{ticker, side, quantity, price, amount}``; sells first.
    """
    market_value = cash + sum(float(holdings.get(t, 0.0)) * p for t, p in prices.items() if p > 0)
    if market_value <= 0:
        return []
    orders: list[dict[str, Any]] = []
    symbols = sorted(set(weights) | {t for t, q in holdings.items() if float(q) > 0})
    for ticker in symbols:
        price = float(prices.get(ticker, 0.0))
        if price <= 0:
            continue
        target_qty = market_value * float(weights.get(ticker, 0.0)) / price
        delta = target_qty - float(holdings.get(ticker, 0.0))
        if abs(delta) * price < MIN_ORDER_AMOUNT or abs(delta) < MIN_ORDER_QTY:
            continue
        qty = round(abs(delta), 6)
        orders.append(
            {
                "ticker": ticker,
                "side": "BUY" if delta > 0 else "SELL",
                "quantity": qty,
                "price": price,
                "amount": round(qty * price, 4),
            }
        )
    orders.sort(key=lambda o: 0 if o["side"] == "SELL" else 1)
    return orders


class PortfolioManagerAgent:
    """Node factory; binds the MPT engine and the LLM gateway."""

    def __init__(
        self,
        portfolio_service: PortfolioService,
        gateway: LLMGateway,
        *,
        risk_free_rate: float = 0.0,
    ) -> None:
        self._mpt = portfolio_service
        self._gateway = gateway
        self._rf = risk_free_rate

    def node(self) -> Callable[[AdvisorState], Awaitable[dict]]:
        """Return the async node function runnable by LangGraph."""

        async def run(state: AdvisorState) -> dict:
            portfolio_id = state.get("portfolio_id")
            risk = state.get("risk") or {}
            market = state.get("market") or {}
            returns_payload = state.get("_returns") or {}

            if not portfolio_id or not risk or not market:
                return {
                    "error": "Portföy Yöneticisi için ön koşullar eksik (risk/piyasa).",
                    "weights": {},
                    "orders": [],
                }

            try:
                # Ağırlıklar ve emirler TEK KEZ hesaplanır ve uygulanır.
                weights = self._compute_weights(market, returns_payload, risk)
                orders = await self._rebalance(int(portfolio_id), weights, market)
            except Exception as exc:  # noqa: BLE001 - görünür hata döndür
                logger.exception("portfolio_manager_failed", error_type=type(exc).__name__)
                return {
                    "weights": {},
                    "orders": [],
                    "report": "",
                    "error": "Portföy yöneticisi yeniden dengelemeyi tamamlayamadı.",
                }

            # LLM gerekçesi: gateway asla istisna fırlatmaz (live → fallback).
            result: GenerationResult[RebalanceRationale] = await self._gateway.generate(
                "rebalance_rationale", self._rationale_context(risk, weights, orders)
            )
            rationale = result.data
            report = "\n".join(
                [rationale.ozet, *[f"• {g}" for g in rationale.gerekceler], DISCLAIMER]
            )
            status = "success" if result.mode != "fallback" else "degraded"
            await self._persist_audit_run(
                state,
                weights=weights,
                orders=orders,
                report=report,
                status=status,
                error=result.error_kind,
            )
            logger.info(
                "portfolio_manager_completed",
                portfolio_id=portfolio_id,
                orders=len(orders),
                llm_mode=result.mode,
            )
            return {
                "weights": weights,
                "orders": orders,
                "report": report,
                "llm_mode": result.mode,
                "llm_error_kind": result.error_kind,
                "error": None,
            }

        return run

    # -- private helpers -----------------------------------------------------

    @staticmethod
    def _as_returns_frame(market: dict, returns_payload: dict) -> pd.DataFrame:
        """Reconstruct an aligned returns DataFrame from the state payload."""
        if returns_payload:
            frame = pd.DataFrame.from_dict(returns_payload, orient="index")
            frame.index = pd.to_datetime(frame.index)
            return frame.sort_index()
        return pd.DataFrame()

    def _compute_weights(
        self,
        market: dict[str, Any],
        returns_payload: dict[str, Any],
        risk: dict[str, Any],
    ) -> dict[str, float]:
        """Run Markowitz; enforce the risk agency's equity ceiling."""
        frame = self._as_returns_frame(market, returns_payload)
        ceiling = float(risk.get("max_equity_weight", 1.0))
        tickers = [t for t in market if isinstance(market[t], dict)]
        if frame.empty or frame.shape[0] < 2 or not tickers:
            weights = {t: ceiling / len(tickers) for t in tickers} if tickers else {}
            logger.warning("markowitz_insufficient_data_equal_weight", tickers=tickers)
            return weights
        weights = self._mpt.tangency_weights(
            frame, risk_free_rate=self._rf, max_equity_weight=ceiling
        )
        return {t: w for t, w in weights.items() if t in market}

    async def _rebalance(
        self, portfolio_id: int, weights: dict[str, float], market: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Apply the orders through the ledger in one DB transaction."""
        if not weights:
            return []
        async with session_factory() as session:
            portfolio = await session.get(Portfolio, portfolio_id)
            if portfolio is None:
                raise RuntimeError(f"Portfolio {portfolio_id} bulunamadı.")
            prices = {
                t: float(snap.get("last_price", 0.0))
                for t, snap in market.items()
                if isinstance(snap, dict)
            }
            orders = build_orders(
                weights, prices, dict(portfolio.holdings or {}), float(portfolio.cash)
            )
            for order in orders:
                await apply_trade(
                    session,
                    portfolio,
                    symbol=str(order["ticker"]),
                    side=str(order["side"]),
                    quantity=order["quantity"],
                    price=order["price"],
                    reason="rebalance",
                )
            await session.commit()
            logger.info("rebalance_persisted", portfolio_id=portfolio_id, orders=len(orders))
            return orders

    @staticmethod
    def _rationale_context(
        risk: dict[str, Any], weights: dict[str, float], orders: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return {
            "risk_kategorisi": risk.get("category"),
            "risk_skoru": risk.get("score"),
            "yontem": "kısıtlı Markowitz (maksimum Sharpe)",
            "hedef_agirliklar": {k: round(v, 4) for k, v in weights.items()},
            "emirler": [
                {"sembol": o["ticker"], "yon": o["side"], "tutar": o["amount"]} for o in orders
            ],
        }

    async def _persist_audit_run(
        self,
        state: AdvisorState,
        *,
        weights: dict[str, float],
        orders: list[dict[str, Any]],
        report: str,
        status: str,
        error: str | None,
    ) -> None:
        """Write the regulatory audit record of this run (``advisor_runs``)."""
        portfolio_id = state.get("portfolio_id")
        customer_id = state.get("customer_id")
        if not portfolio_id or not customer_id:
            logger.warning("audit_run_skipped_missing_context")
            return
        try:
            async with session_factory() as session:
                session.add(
                    AdvisorRun(
                        portfolio_id=int(portfolio_id),
                        customer_id=int(customer_id),
                        market_input=state.get("market") or {},
                        target_weights=weights,
                        orders=orders,
                        report=report,
                        status=status,
                        error=error,
                    )
                )
                await session.commit()
        except Exception as exc:  # noqa: BLE001 - iz yazımı ana akışı bozmamalı
            logger.exception("audit_run_failed", error_type=type(exc).__name__)


__all__ = ["PortfolioManagerAgent", "build_orders"]
