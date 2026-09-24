"""LangGraph state schema for the Robo-Advisor workflow.

The state is a :class:`~typing.TypedDict` describing every field LangGraph
propagates between nodes and persists in the checkpointer. Keeping it
JSON-serialisable (no arbitrary objects, no DataFrames) means the whole graph
state — including a mid-run snapshot — can be checkpointed to SQLite and
replayed, which is a production requirement for audits.

Field semantics:
    * ``customer_id`` / ``portfolio_id`` — immutable run parameters (input).
    * ``holdings`` — input positions {ticker: quantity} read from the DB.
    * ``market``  — per-ticker snapshot dicts produced by the Market Agent.
    * ``_returns`` — aligned returns frame (JSON-ified, date-indexed) so the
      Portfolio Manager can rebuild the DataFrame used by Markowitz.
    * ``risk``    — :class:`services.risk_service.RiskAssessment` payload.
    * ``weights`` — final target weights (ticker -> weight in [0,1]).
    * ``orders``  — rebalancing orders computed by the Portfolio Manager.
    * ``report``  — the LLM narrative written back to the API response.
    * ``error``   — non-null when a node failed; short-circuits the graph.
"""

from __future__ import annotations

from typing import TypedDict


class AdvisorState(TypedDict, total=False):
    customer_id: int
    portfolio_id: int

    # Girdi: portföyün mevcut pozisyonları {ticker: quantity}
    holdings: dict[str, float]

    # Piyasa Ajanı çıktısı
    market: dict[str, object]  # {ticker: {last_price, momentum_1m, volatility_annualized}}
    _returns: dict[str, object]  # {date: {ticker: ret}} — aligned returns

    # Risk Ajanı çıktısı
    risk: dict[str, object]  # {score, category, max_equity_weight, rationale}

    # Portföy Yöneticisi çıktısı
    weights: dict[str, float]  # {ticker: weight}
    orders: list[dict[str, object]]  # [{ticker, side, quantity, price, amount}]

    # Raporlama
    report: str

    # Hata yönetimi
    error: str | None
