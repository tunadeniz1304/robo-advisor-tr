"""Hash-chained audit log.

Each record stores ``prev_hash`` and ``hash = sha256(prev_hash ‖ canonical)``
where ``canonical`` is the sorted-key JSON of the record's content. Any edit,
deletion or insertion in the middle breaks the chain; :func:`verify_chain`
recomputes it end to end (``GET /api/v1/audit/verify``).

Appends are serialised with a process-wide lock and written in their own
session right after the business transaction commits.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select

from core.database import session_factory
from core.locks import get_lock_manager
from core.logging import get_logger
from models import AuditLog
from models.base import utcnow

logger = get_logger("otonom.audit")

GENESIS_HASH = "0" * 64


def _lock() -> Any:
    """Serialise appends to the hash chain (shared across processes with Redis)."""
    return get_lock_manager().lock("audit:chain")


def _normalise(payload: Any) -> Any:
    return json.loads(json.dumps(payload or {}, default=str, ensure_ascii=False))


def canonical(
    *,
    created_at: datetime,
    actor: str,
    actor_role: str | None,
    action: str,
    entity_type: str,
    entity_id: str | None,
    customer_id: int | None,
    payload: Any,
) -> str:
    """Canonical JSON used for hashing (stable key order and formatting)."""
    record = {
        "created_at": created_at.isoformat(timespec="microseconds"),
        "actor": actor,
        "actor_role": actor_role,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "customer_id": customer_id,
        "payload": payload,
    }
    return json.dumps(
        record, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )


def chain_hash(prev_hash: str, canonical_json: str) -> str:
    return hashlib.sha256((prev_hash + canonical_json).encode("utf-8")).hexdigest()


def _row_hash(row: AuditLog) -> str:
    return chain_hash(
        row.prev_hash,
        canonical(
            created_at=row.created_at,
            actor=row.actor,
            actor_role=row.actor_role,
            action=row.action,
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            customer_id=row.customer_id,
            payload=row.payload,
        ),
    )


async def record_audit(
    *,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: Any = None,
    actor_role: str | None = None,
    customer_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> AuditLog:
    """Append one record to the chain (own session, committed)."""
    async with _lock():
        async with session_factory() as session:
            last = (
                await session.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(1))
            ).scalar_one_or_none()
            prev = last.hash if last is not None else GENESIS_HASH
            created = utcnow()
            body = _normalise(payload)
            eid = str(entity_id) if entity_id is not None else None
            row = AuditLog(
                created_at=created,
                actor=actor,
                actor_role=actor_role,
                action=action,
                entity_type=entity_type,
                entity_id=eid,
                customer_id=customer_id,
                payload=body,
                prev_hash=prev,
                hash="",
            )
            row.hash = _row_hash(row)
            session.add(row)
            await session.commit()
            await session.refresh(row)
    logger.info("audit_recorded", action=action, entity_type=entity_type, entity_id=eid)
    return row


@dataclass(frozen=True)
class ChainStatus:
    """Result of a chain verification."""

    valid: bool
    count: int
    broken_at: int | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "count": self.count,
            "broken_at": self.broken_at,
            "reason": self.reason,
        }


async def verify_chain() -> ChainStatus:
    """Recompute the full chain and report the first broken link."""
    async with session_factory() as session:
        rows = (await session.execute(select(AuditLog).order_by(AuditLog.id))).scalars().all()
    prev = GENESIS_HASH
    for row in rows:
        if row.prev_hash != prev:
            return ChainStatus(False, len(rows), row.id, "Önceki hash bağlantısı kopuk.")
        if _row_hash(row) != row.hash:
            return ChainStatus(False, len(rows), row.id, "Kayıt içeriği değiştirilmiş.")
        prev = row.hash
    return ChainStatus(True, len(rows))


__all__ = ["ChainStatus", "GENESIS_HASH", "canonical", "chain_hash", "record_audit", "verify_chain"]
