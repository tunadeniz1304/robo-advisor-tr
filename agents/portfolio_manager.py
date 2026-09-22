"""Portföy Yöneticisi (Portfolio Manager) — LangGraph node.

Combines the three analytic pillars of the system:

    1. **Markowitz MPT** (:class:`services.portfolio_service.PortfolioService`)
       computes target weights from real returns data.
    2. **Real LLM analysis** (:class:`llm.clients.LLMClient`) evaluates those
       weights against the risk profile and market outlook (modular prompt —
       :mod:`agents.prompts`).
    3. **Database rebalancing** — the node persists the resulting orders via
       SQL: ``UPDATE portfolios SET holdings = …, cash = …`` and ``INSERT INTO
       transactions`` for each BUY/SELL, so the ledger and positions stay
       consistent.

Weights are reconciled with the risk agency's equity ceiling. The LLM is used
for qualitative gerekçe (rationale); the *binding* weights come from the MPT
optimizer — the LLM may adjust them within the risk ceiling, but never
override the ceiling itself.
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

import pandas as pd
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from agents.prompts import PORTFOLIO_MANAGER_SYSTEM, PORTFOLIO_MANAGER_USER
from agents.state import AdvisorState
from core.database import session_factory
from core.logging import get_logger
from llm.clients import LLMClient, LLMProviderError
from models import Portfolio, Transaction
from services.portfolio_service import PortfolioService

logger = get_logger("otonom.agent.portfolio")


class PortfolioManagerAgent:
    """Node factory; binds the MPT engine and the real LLM client."""

    def __init__(self, portfolio_service: PortfolioService, llm: LLMClient) -> None:
        self._mpt = portfolio_service
        self._llm = llm

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
                weights = await self._compute_weights(market, returns_payload, risk)
                orders = await self._rebalance(portfolio_id, weights, market)
                report = await self._llm_analysis(state, weights, orders)
            except LLMProviderError as exc:
                # LLM erişilemez durumda: MPT ağırlıkları hâlâ uygulanabilir —
                # ama kullanıcıya bunu açıkça raporla.
                logger.warning("portfolio_manager_llm_unavailable", error=str(exc))
                weights = await self._compute_weights(market, returns_payload, risk)
                orders = await self._rebalance(portfolio_id, weights, market)
                report = (
                    "Otomatik yeniden dengeleme tamamlandı; LLM analizi şu an "
                    f"erişilemediği için atlandı ({exc}). Ağırlıklar Markowitz "
                    "optimizasyonuna dayanmaktadır."
                )
                return {"weights": weights, "orders": orders, "report": report, "error": None}

            except Exception as exc:  # noqa: BLE001 - görünür hata döndür
                logger.exception("portfolio_manager_failed", error=str(exc))
                return {
                    "weights": {},
                    "orders": [],
                    "report": "",
                    "error": f"Portföy yöneticisi başarısız: {exc}",
                }

            logger.info(
                "portfolio_manager_completed",
                portfolio_id=portfolio_id,
                orders=len(orders),
                weights={k: round(v, 4) for k, v in weights.items()},
            )
            return {"weights": weights, "orders": orders, "report": report, "error": None}

        return run

    # -- private helpers -----------------------------------------------------

    @staticmethod
    def _as_returns_frame(market: dict, returns_payload: dict) -> pd.DataFrame:
        """Reconstruct an aligned returns DataFrame from the state payload."""
        if returns_payload:
            frame = pd.DataFrame.from_dict(returns_payload, orient="index")
            frame.index = pd.to_datetime(frame.index)
            return frame
        # Fallback: daily_returns float dict'lerinden yeniden kur.
        series: dict[str, pd.Series] = {}
        for ticker, snap in market.items():
            dr = snap.get("_daily_returns")
            if isinstance(dr, dict) and dr:
                series[ticker] = pd.Series(dr)
        return pd.DataFrame(series)

    async def _compute_weights(
        self,
        market: dict[str, object],
        returns_payload: dict[str, object],
        risk: dict[str, object],
    ) -> dict[str, float]:
        """Run Markowitz; enforce the risk agency's equity ceiling."""
        frame = self._as_returns_frame(market, returns_payload)
        ceiling = float(risk.get("max_equity_weight", 1.0))
        tickers = [t for t in market if isinstance(market[t], dict)]
        if frame.empty or frame.shape[0] < 2 or not tickers:
            # Yetersiz veri: eşit ağırlık — veri olmadan MPT çalışmaz.
            weights = {t: 1.0 / len(tickers) for t in tickers} if tickers else {}
            logger.warning("markowitz_insufficient_data_equal_weight", tickers=tickers)
        else:
            weights = self._mpt.tangency_weights(frame, risk_free_rate=0.0, max_equity_weight=ceiling)
            # Sadece state'te var olan, verisi olan varlıkları döndür.
            weights = {t: w for t, w in weights.items() if t in market}
        return weights

    async def _rebalance(
        self,
        portfolio_id: int,
        weights: dict[str, float],
        market: dict[str, object],
    ) -> list[dict[str, object]]:
        """Persist rebalanced holdings (SQL UPDATE + INSERTs)."""
        if not weights:
            return []

        async with session_factory() as session:
            portfolio = await session.get(Portfolio, portfolio_id)
            if portfolio is None:
                raise RuntimeError(f"Portfolio {portfolio_id} bulunamadı.")

            prices = {
                t: float(snap.get("last_price", 0.0)) for t, snap in market.items() if isinstance(snap, dict)
            }
            current = dict(portfolio.holdings or {})
            cash_before = float(portfolio.cash)

            # Piyasa değeri (T0): nakit + Σ qty*price
            market_value = cash_before + sum(
                float(current.get(t, 0.0)) * p for t, p in prices.items() if p > 0
            )
            if market_value <= 0 or not prices:
                return []

            orders: list[dict[str, object]] = []
            new_holdings: dict[str, float] = {}
            cash_after = cash_before

            for ticker, weight in weights.items():
                price = prices.get(ticker, 0.0)
                if price <= 0:
                    new_holdings[ticker] = float(current.get(ticker, 0.0))
                    continue
                target_value = market_value * weight
                target_qty = target_value / price
                current_qty = float(current.get(ticker, 0.0))
                delta = target_qty - current_qty

                # Küçük eşiklerin altında işlem yapma (sürtünme maliyeti).
                if abs(delta) * price >= 10.0 and abs(delta) >= 0.01:
                    side = "BUY" if delta > 0 else "SELL"
                    qty = round(abs(delta), 6)
                    amount = round(qty * price, 4)
                    orders.append(
                        {
                            "ticker": ticker,
                            "side": side,
                            "quantity": qty,
                            "price": price,
                            "amount": amount,
                        }
                    )
                    cash_after += (amount if side == "SELL" else -amount)

                new_holdings[ticker] = round(target_qty, 6)

            # SQL UPDATE: holdings + cash
            await session.execute(
                update(Portfolio)
                .where(Portfolio.id == portfolio_id)
                .values(holdings={k: float(v) for k, v in new_holdings.items()}, cash=round(cash_after, 4))
            )

            # SQL INSERT: her işlem için ledger kaydı
            for order in orders:
                session.add(
                    Transaction(
                        portfolio_id=portfolio_id,
                        ticker=str(order["ticker"]),
                        side=str(order["side"]),
                        quantity=float(order["quantity"]),
                        price=float(order["price"]),
                        total_amount=float(order["amount"]),
                        reason="rebalance",
                    )
                )
            await session.commit()

            logger.info(
                "rebalance_persisted",
                portfolio_id=portfolio_id,
                cash=round(cash_after, 4),
                orders=len(orders),
            )
            return orders

    async def _llm_analysis(
        self,
        state: AdvisorState,
        weights: dict[str, float],
        orders: list[dict[str, object]],
    ) -> str:
        """Run the real LLM narrative for the computed allocation."""
        risk = state.get("risk") or {}
        market = state.get("market") or {}
        user_prompt = PORTFOLIO_MANAGER_USER.format(
            risk_profile=json.dumps(risk, ensure_ascii=False, indent=2),
            markowitz_weights=json.dumps(weights, ensure_ascii=False, indent=2),
            market_snapshot=json.dumps(market, ensure_ascii=False, indent=2)[:4000],
            current_holdings=json.dumps(state.get("holdings") or {}, ensure_ascii=False),
        )
        return await self._llm.complete(
            system=PORTFOLIO_MANAGER_SYSTEM, user=user_prompt, max_tokens=700
        )


__all__ = ["PortfolioManagerAgent"]
