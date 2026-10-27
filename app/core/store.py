"""Shared state: rate counters, concurrency slots, circuit breakers, idempotency.

Two backends behind one interface:

* ``RedisStore``  - required for more than one gateway instance (brief §2/§4.2).
* ``MemoryStore`` - single-process fallback for tests and local development.

The gateway never fails a request because Redis is missing; it degrades to
per-instance counters and logs a warning at startup. That is a deliberate
trade-off, documented in README: with Redis down, limits are enforced per
instance rather than globally.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Protocol

from app.config import settings

log = logging.getLogger("gateway.store")


class Store(Protocol):
    async def close(self) -> None: ...

    async def incr_window(self, key: str, window_seconds: int, amount: int = 1) -> int: ...

    async def acquire_slot(self, key: str, limit: int, ttl: int) -> bool: ...

    async def release_slot(self, key: str) -> None: ...

    async def in_flight(self, key: str) -> int: ...

    async def breaker_get(self, provider_id: int) -> dict | None: ...

    async def breaker_set(self, provider_id: int, state: dict, ttl: int) -> None: ...

    async def breaker_clear(self, provider_id: int) -> None: ...

    async def idem_begin(self, key: str, ttl: int) -> bool: ...

    async def idem_get(self, key: str) -> dict | None: ...

    async def idem_store(self, key: str, value: dict, ttl: int) -> None: ...

    async def idem_release(self, key: str) -> None: ...

    async def healthy(self) -> bool: ...


# ------------------------------------------------------------------ memory
class MemoryStore:
    """Single-process implementation. Not shared between instances."""

    def __init__(self) -> None:
        self._windows: dict[str, list[float]] = {}
        self._slots: dict[str, dict[str, float]] = {}
        self._breakers: dict[int, tuple[dict, float]] = {}
        self._idem_locks: dict[str, float] = {}
        self._idem_values: dict[str, tuple[dict, float]] = {}
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        return None

    async def healthy(self) -> bool:
        return True

    async def incr_window(self, key: str, window_seconds: int, amount: int = 1) -> int:
        now = time.time()
        async with self._lock:
            hits = [t for t in self._windows.get(key, []) if now - t < window_seconds]
            hits.extend([now] * max(1, amount))
            self._windows[key] = hits
            return len(hits)

    async def acquire_slot(self, key: str, limit: int, ttl: int) -> bool:
        now = time.time()
        async with self._lock:
            held = {
                token: ts
                for token, ts in self._slots.get(key, {}).items()
                if now - ts < ttl
            }
            if len(held) >= limit:
                self._slots[key] = held
                return False
            held[uuid.uuid4().hex] = now
            self._slots[key] = held
            return True

    async def release_slot(self, key: str) -> None:
        async with self._lock:
            held = self._slots.get(key, {})
            if held:
                # Release the oldest slot; order does not matter for a count.
                held.pop(next(iter(held)))
            self._slots[key] = held

    async def in_flight(self, key: str) -> int:
        return len(self._slots.get(key, {}))

    async def breaker_get(self, provider_id: int) -> dict | None:
        entry = self._breakers.get(provider_id)
        if not entry:
            return None
        state, expires = entry
        if time.time() > expires:
            self._breakers.pop(provider_id, None)
            return None
        return dict(state)

    async def breaker_set(self, provider_id: int, state: dict, ttl: int) -> None:
        self._breakers[provider_id] = (dict(state), time.time() + ttl)

    async def breaker_clear(self, provider_id: int) -> None:
        self._breakers.pop(provider_id, None)

    async def idem_begin(self, key: str, ttl: int) -> bool:
        now = time.time()
        async with self._lock:
            expiry = self._idem_locks.get(key)
            if expiry and expiry > now:
                return False
            self._idem_locks[key] = now + ttl
            return True

    async def idem_get(self, key: str) -> dict | None:
        entry = self._idem_values.get(key)
        if not entry:
            return None
        value, expires = entry
        if time.time() > expires:
            self._idem_values.pop(key, None)
            return None
        return dict(value)

    async def idem_store(self, key: str, value: dict, ttl: int) -> None:
        self._idem_values[key] = (dict(value), time.time() + ttl)

    async def idem_release(self, key: str) -> None:
        """Drop only the in-flight lock, keeping any stored response."""
        self._idem_locks.pop(key, None)


# ------------------------------------------------------------------- redis
class RedisStore:
    """Shared implementation used by every instance in production."""

    def __init__(self, url: str, prefix: str = "gw") -> None:
        import redis.asyncio as redis

        self._client = redis.from_url(url, encoding="utf-8", decode_responses=True)
        self._prefix = prefix

    def _k(self, *parts: str | int) -> str:
        return ":".join([self._prefix, *(str(p) for p in parts)])

    async def close(self) -> None:
        await self._client.aclose()

    async def healthy(self) -> bool:
        try:
            return bool(await self._client.ping())
        except Exception:
            return False

    async def incr_window(self, key: str, window_seconds: int, amount: int = 1) -> int:
        """Fixed-window counter. Window resets when the key expires."""
        full = self._k("win", key)
        pipe = self._client.pipeline()
        pipe.incrby(full, max(1, amount))
        pipe.expire(full, window_seconds, nx=True)
        count, _ = await pipe.execute()
        return int(count)

    async def acquire_slot(self, key: str, limit: int, ttl: int) -> bool:
        """Concurrency slot via a sorted set of timestamps.

        Stale entries expire on their own, so a crashed instance cannot leak
        capacity forever - this is the guarantee brief §5.2 asks for.
        """
        full = self._k("conc", key)
        now = time.time()
        token = f"{now}:{uuid.uuid4().hex}"

        pipe = self._client.pipeline()
        pipe.zremrangebyscore(full, "-inf", now - ttl)
        pipe.zadd(full, {token: now})
        pipe.zcard(full)
        pipe.expire(full, ttl)
        _, _, count, _ = await pipe.execute()

        if int(count) > limit:
            await self._client.zrem(full, token)
            return False
        return True

    async def release_slot(self, key: str) -> None:
        full = self._k("conc", key)
        # Remove the oldest entry: any slot is equivalent for a pure count.
        oldest = await self._client.zrange(full, 0, 0)
        if oldest:
            await self._client.zrem(full, oldest[0])

    async def in_flight(self, key: str) -> int:
        return int(await self._client.zcard(self._k("conc", key)))

    async def breaker_get(self, provider_id: int) -> dict | None:
        raw = await self._client.get(self._k("breaker", provider_id))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def breaker_set(self, provider_id: int, state: dict, ttl: int) -> None:
        await self._client.set(
            self._k("breaker", provider_id), json.dumps(state), ex=max(1, ttl)
        )

    async def breaker_clear(self, provider_id: int) -> None:
        await self._client.delete(self._k("breaker", provider_id))

    async def idem_begin(self, key: str, ttl: int) -> bool:
        return bool(await self._client.set(self._k("idem_lock", key), "1", nx=True, ex=ttl))

    async def idem_get(self, key: str) -> dict | None:
        raw = await self._client.get(self._k("idem", key))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def idem_store(self, key: str, value: dict, ttl: int) -> None:
        await self._client.set(self._k("idem", key), json.dumps(value), ex=ttl)
        await self._client.delete(self._k("idem_lock", key))

    async def idem_release(self, key: str) -> None:
        """Drop only the in-flight lock, keeping any stored response."""
        await self._client.delete(self._k("idem_lock", key))


_store: Store | None = None


def build_store() -> Store:
    global _store
    if _store is not None:
        return _store

    if settings.redis_url:
        try:
            _store = RedisStore(settings.redis_url, settings.redis_prefix)
            log.info("Shared state: Redis at %s", settings.redis_url)
        except Exception:
            log.exception("Could not create the Redis client; falling back to memory.")
            _store = MemoryStore()
    else:
        log.warning(
            "REDIS_URL is not set: rate limits, concurrency caps and circuit breakers "
            "are per-instance. Use Redis when running more than one gateway instance."
        )
        _store = MemoryStore()
    return _store


def get_store() -> Store:
    return _store or build_store()


async def close_store() -> None:
    global _store
    if _store is not None:
        await _store.close()
        _store = None
