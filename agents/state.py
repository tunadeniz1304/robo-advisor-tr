"""LangGraph state schema of the rebalancing workflow.

The state is a JSON-serialisable :class:`~typing.TypedDict`, so the durable
checkpointer can persist a run that is paused at the human-approval
interrupt and resume it later (even after a restart).

Field groups:
    * inputs — ``portfolio_id``, ``customer_id``, actor, source/trigger,
      optimiser method, override acknowledgement, idempotency key;
    * node outputs — ``market`` (data source summary), ``level``
      (suitability), ``optimization`` (target + explainability),
      ``proposal_id``/``proposal_status``;
    * approval — ``decision`` written by the resume command;
    * reporting — ``report``, ``llm_mode``, ``error``.
"""

from __future__ import annotations

from typing import Any, TypedDict


class RebalanceState(TypedDict, total=False):
    # Girdiler
    portfolio_id: int
    customer_id: int
    actor: str
    actor_role: str | None
    user_id: int | None
    source: str
    trigger: str
    method: str | None
    override_ack: bool
    idempotency_key: str | None
    regime: dict[str, Any] | None

    # Düğüm çıktıları
    market: dict[str, Any]
    level: dict[str, Any]
    optimization: dict[str, Any]
    proposal_id: int | None
    proposal_status: str | None
    needs_rebalance: bool
    message: str

    # İnsan onayı
    decision: dict[str, Any]
    execution: dict[str, Any]

    # Raporlama
    report: str
    llm_mode: str | None
    error: str | None


# Geriye dönük uyumluluk: eski ad.
AdvisorState = RebalanceState

__all__ = ["AdvisorState", "RebalanceState"]
