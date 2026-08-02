"""TTL cache with single-flight and stale-on-error.

Two behaviours here earn their keep:

**Single-flight** -- the Overview page polls several endpoints that share a
source. Without it, five concurrent misses spawn five ``rocm-smi`` processes.
Concurrent callers instead await the one in-flight refresh.

**Stale-on-error** -- when a refresh fails, the last good value is returned with
``stale=True`` so a card dims rather than blanking. A transient timeout should
not erase the reading you were watching.

This assumes a SINGLE uvicorn worker. The state is per-process; adding
``--workers N`` silently gives each worker its own cache and its own subprocess
storm. That constraint is documented in the unit file and the README.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass
class Entry(Generic[T]):
    value: T
    stored_at: float
    stale: bool = False

    @property
    def age_ms(self) -> float:
        return (time.monotonic() - self.stored_at) * 1000


class Cache:
    def __init__(self) -> None:
        self._entries: dict[str, Entry[Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        return lock

    def peek(self, key: str) -> Entry[Any] | None:
        return self._entries.get(key)

    def invalidate(self, key: str | None = None) -> None:
        if key is None:
            self._entries.clear()
        else:
            self._entries.pop(key, None)

    def store(self, key: str, value: Any) -> Entry[Any]:
        entry = Entry(value=value, stored_at=time.monotonic())
        self._entries[key] = entry
        return entry

    async def get(
        self,
        key: str,
        producer: Callable[[], Awaitable[T]],
        *,
        ttl: float | None,
        force: bool = False,
    ) -> Entry[T]:
        """Return a cached entry, refreshing it when stale.

        ``ttl=None`` means "compute once, then never expire" -- used for
        rocminfo (slow, and a static description of the hardware).
        """
        entry = self._entries.get(key)
        if not force and entry is not None and _fresh(entry, ttl):
            return entry

        async with self._lock(key):
            # Another waiter may have refreshed while we queued.
            entry = self._entries.get(key)
            if not force and entry is not None and _fresh(entry, ttl):
                return entry

            try:
                value = await producer()
            except Exception:
                previous = self._entries.get(key)
                if previous is not None:
                    previous.stale = True
                    return previous
                raise
            return self.store(key, value)


def _fresh(entry: Entry[Any], ttl: float | None) -> bool:
    if ttl is None:
        return True
    return (time.monotonic() - entry.stored_at) < ttl


cache = Cache()
