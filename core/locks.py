"""Named asynchronous locks: in-process or shared through Redis.

``LOCK_BACKEND=memory`` keeps the single-process behaviour (``asyncio.Lock``
per event loop and name). ``LOCK_BACKEND=redis`` makes the critical sections
— one writer per portfolio, the audit hash chain — exclusive across several
application processes/containers. The database still guards every state
transition with a conditional ``UPDATE``; the lock prevents two processes
from doing the expensive work (pricing, broker fills) concurrently.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol

DEFAULT_TTL_SECONDS = 60.0
DEFAULT_WAIT_SECONDS = 30.0
POLL_SECONDS = 0.02
KEY_PREFIX = "lock:"


class LockTimeoutError(TimeoutError):
    """Raised when a shared lock cannot be acquired in time."""


class LockManager(Protocol):
    backend: str

    def lock(self, name: str) -> Any:
        """Async context manager holding the lock ``name``."""


class MemoryLockManager:
    """``asyncio.Lock`` per (event loop, name) — single process only."""

    backend = "memory"

    def __init__(self) -> None:
        self._locks: dict[tuple[int, str], asyncio.Lock] = {}

    @asynccontextmanager
    async def lock(self, name: str) -> AsyncIterator[None]:
        key = (id(asyncio.get_running_loop()), name)
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        async with lock:
            yield


class RedisLockManager:
    """Redis locks (``redis.asyncio`` Lock: token + TTL, atomic release)."""

    backend = "redis"

    def __init__(
        self,
        client: Any = None,
        *,
        factory: Any = None,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        wait_seconds: float = DEFAULT_WAIT_SECONDS,
    ) -> None:
        if client is None and factory is None:
            raise ValueError("Redis istemcisi veya fabrikası gerekli.")
        self._fixed = client
        self._factory = factory
        self._clients: dict[int, Any] = {}
        self._ttl = ttl_seconds
        self._wait = wait_seconds

    def _client(self) -> Any:
        """Async clients are bound to an event loop: one client per loop."""
        if self._fixed is not None:
            return self._fixed
        key = id(asyncio.get_running_loop())
        client = self._clients.get(key)
        if client is None:
            client = self._clients[key] = self._factory()
        return client

    @asynccontextmanager
    async def lock(self, name: str) -> AsyncIterator[None]:
        lock = self._client().lock(
            KEY_PREFIX + name,
            timeout=self._ttl,
            sleep=POLL_SECONDS,
            blocking_timeout=self._wait,
        )
        if not await lock.acquire():
            raise LockTimeoutError(f"Kilit alınamadı: {name}")
        try:
            yield
        finally:
            await lock.release()


def build_lock_manager(
    backend: str, *, redis_client: Any = None, redis_url: str | None = None
) -> MemoryLockManager | RedisLockManager:
    """Lock manager for ``memory`` or ``redis``."""
    if backend == "memory":
        return MemoryLockManager()
    if backend == "redis":
        if redis_client is not None:
            return RedisLockManager(redis_client)
        if not redis_url:
            raise ValueError("Redis kilidi için REDIS_URL gerekli.")
        from core import redis_client as rc

        url = redis_url
        return RedisLockManager(factory=lambda: rc.async_client(url))
    raise ValueError(f"Bilinmeyen kilit altyapısı: {backend}")


_GLOBAL: list[MemoryLockManager | RedisLockManager] = [MemoryLockManager()]


def get_lock_manager() -> MemoryLockManager | RedisLockManager:
    """Process-wide lock manager (set by the application on startup)."""
    return _GLOBAL[0]


def set_lock_manager(manager: MemoryLockManager | RedisLockManager) -> None:
    _GLOBAL[0] = manager


__all__ = [
    "LockManager",
    "LockTimeoutError",
    "MemoryLockManager",
    "RedisLockManager",
    "build_lock_manager",
    "get_lock_manager",
    "set_lock_manager",
]
