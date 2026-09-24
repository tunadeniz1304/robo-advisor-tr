"""Redis client factories (one place to swap them in tests).

The shared lock and rate-limit backends use Redis when ``LOCK_BACKEND`` /
``RATE_LIMIT_STORAGE`` are ``redis``. Tests replace these factories with
``fakeredis`` clients bound to one in-memory server, which simulates several
application processes sharing a Redis instance.
"""

from __future__ import annotations

from typing import Any


def async_client(url: str) -> Any:
    """``redis.asyncio`` client for ``url``."""
    import redis.asyncio as redis_async

    return redis_async.from_url(url, decode_responses=True)


def sync_client(url: str) -> Any:
    """Blocking ``redis`` client for ``url`` (rate limiting in middleware)."""
    import redis

    return redis.Redis.from_url(url, decode_responses=True)


__all__ = ["async_client", "sync_client"]
