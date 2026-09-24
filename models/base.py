"""Shared ORM helpers (timestamps)."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Return naive UTC now (portable across SQLite and PostgreSQL)."""
    return datetime.now(UTC).replace(tzinfo=None)


__all__ = ["utcnow"]
