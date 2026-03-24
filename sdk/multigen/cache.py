"""Multi-tier async caching system with LRU, TTL, stampede protection, and decorators."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Tuple


# ── Cache Entry ───────────────────────────────────────────────────────────────

@dataclass
class CacheEntry:
    value: Any
    created_at: float = field(default_factory=time.time)
    ttl: Optional[float] = None
    hits: int = 0

    @property
    def is_expired(self) -> bool:
        return self.ttl is not None and time.time() > self.created_at + self.ttl

    def touch(self) -> None:
        self.hits += 1


# ── LRU Cache ─────────────────────────────────────────────────────────────────

class LRUCache:
    """
    Synchronous LRU cache with optional per-entry TTL.

    Usage::

        cache = LRUCache(maxsize=256, default_ttl=60)
        cache.set("key", {"data": 42})
        value = cache.get("key")
        print(cache.stats())
    """

    def __init__(
        self,
        maxsize: int = 256,
        default_ttl: Optional[float] = None,
    ) -> None:
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self.maxsize = maxsize
        self.default_ttl = default_ttl
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Any]:
        if key not in self._cache:
            self._misses += 1
            return None
        entry = self._cache[key]
        if entry.is_expired:
            del self._cache[key]
            self._misses += 1
            return None
        self._cache.move_to_end(key)
        entry.touch()
        self._hits += 1
        return entry.value

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        effective_ttl = ttl if ttl is not None else self.default_ttl
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = CacheEntry(value=value, ttl=effective_ttl)
        if len(self._cache) > self.maxsize:
            self._cache.popitem(last=False)

    def delete(self, key: str) -> bool:
        return self._cache.pop(key, None) is not None

    def clear(self) -> None:
        self._cache.clear()
        self._hits = 0
        self._misses = 0

    def stats(self) -> Dict[str, Any]:
        total = self._hits + self._misses
        return {
            "size": len(self._cache),
            "maxsize": self.maxsize,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / total if total else 0.0,
        }

    def __len__(self) -> int:
        return len(self._cache)

    def __contains__(self, key: str) -> bool:
        entry = self._cache.get(key)
        if entry is None:
            return False
        if entry.is_expired:
            del self._cache[key]
            return False
        return True


# ── TTL Cache ─────────────────────────────────────────────────────────────────

class TTLCache:
    """
    Simple TTL-only cache — all entries expire after a fixed duration by default.
    Expired entries are evicted lazily on access and eagerly before insertions.

    Usage::

        cache = TTLCache(default_ttl=120, maxsize=1000)
        cache.set("token", "abc", ttl=30)
        value = cache.get("token")
    """

    def __init__(
        self,
        default_ttl: float = 300.0,
        maxsize: int = 1024,
    ) -> None:
        self._store: Dict[str, CacheEntry] = {}
        self.default_ttl = default_ttl
        self.maxsize = maxsize

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        if entry.is_expired:
            del self._store[key]
            return None
        entry.touch()
        return entry.value

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        self._evict_expired()
        if len(self._store) >= self.maxsize:
            oldest = min(self._store, key=lambda k: self._store[k].created_at)
            del self._store[oldest]
        self._store[key] = CacheEntry(value=value, ttl=ttl if ttl is not None else self.default_ttl)

    def invalidate(self, key: str) -> bool:
        return self._store.pop(key, None) is not None

    def clear(self) -> None:
        self._store.clear()

    def _evict_expired(self) -> int:
        expired = [k for k, e in self._store.items() if e.is_expired]
        for k in expired:
            del self._store[k]
        return len(expired)

    def __len__(self) -> int:
        self._evict_expired()
        return len(self._store)


# ── Async Cache ───────────────────────────────────────────────────────────────

class AsyncCache:
    """
    Async-safe LRU cache with stampede (thundering-herd) protection via
    single-flight deduplication.

    Usage::

        cache = AsyncCache(maxsize=512, default_ttl=60)

        async def fetch_data(user_id: str) -> dict:
            return await db.get_user(user_id)

        result = await cache.get_or_compute(
            f"user:{user_id}", fetch_data, ttl=30, user_id
        )
    """

    def __init__(
        self,
        maxsize: int = 256,
        default_ttl: Optional[float] = None,
    ) -> None:
        self._lru = LRUCache(maxsize=maxsize, default_ttl=default_ttl)
        self._lock = asyncio.Lock()
        self._inflight: Dict[str, asyncio.Event] = {}

    async def get(self, key: str) -> Optional[Any]:
        async with self._lock:
            return self._lru.get(key)

    async def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        async with self._lock:
            self._lru.set(key, value, ttl=ttl)
            event = self._inflight.pop(key, None)
        if event:
            event.set()

    async def delete(self, key: str) -> bool:
        async with self._lock:
            return self._lru.delete(key)

    async def clear(self) -> None:
        async with self._lock:
            self._lru.clear()

    async def get_or_compute(
        self,
        key: str,
        compute_fn: Callable,
        ttl: Optional[float] = None,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """
        Return cached value or compute it.  If multiple coroutines race for the
        same key, only one computation runs (stampede protection).
        """
        # Fast path — value already cached
        async with self._lock:
            value = self._lru.get(key)
            if value is not None:
                return value
            # Slow path — check if a concurrent fetch is in flight
            if key in self._inflight:
                event = self._inflight[key]
            else:
                event = asyncio.Event()
                self._inflight[key] = event
                event = None  # this coroutine is the designated fetcher

        if event is not None:
            # Another coroutine is fetching — wait for it
            await event.wait()
            return await self.get(key)

        # This coroutine does the computation
        try:
            result = compute_fn(*args, **kwargs)
            if asyncio.iscoroutine(result):
                result = await result
            await self.set(key, result, ttl=ttl)
            return result
        except Exception:
            async with self._lock:
                ev = self._inflight.pop(key, None)
            if ev:
                ev.set()
            raise

    async def stats(self) -> Dict[str, Any]:
        async with self._lock:
            return self._lru.stats()


# ── Multi-Tier Cache ──────────────────────────────────────────────────────────

class MultiTierCache:
    """
    Write-through two-tier cache: L1 (fast, small) → L2 (slower, larger).

    On read: L1 hit → return immediately.
             L1 miss + L2 hit → backfill L1, return value.
             Both miss → caller must compute.

    Usage::

        cache = MultiTierCache(
            l1=AsyncCache(maxsize=64, default_ttl=30),
            l2=AsyncCache(maxsize=1024, default_ttl=600),
        )
        await cache.set("result", data)
        value = await cache.get("result")
        value = await cache.get_or_compute("result", expensive_fn, ttl=60, arg1)
    """

    def __init__(
        self,
        l1: Optional[AsyncCache] = None,
        l2: Optional[AsyncCache] = None,
    ) -> None:
        self.l1 = l1 or AsyncCache(maxsize=64, default_ttl=30)
        self.l2 = l2 or AsyncCache(maxsize=512, default_ttl=300)

    async def get(self, key: str) -> Optional[Any]:
        value = await self.l1.get(key)
        if value is not None:
            return value
        value = await self.l2.get(key)
        if value is not None:
            await self.l1.set(key, value)
        return value

    async def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        await self.l1.set(key, value, ttl=ttl)
        await self.l2.set(key, value, ttl=ttl)

    async def delete(self, key: str) -> None:
        await self.l1.delete(key)
        await self.l2.delete(key)

    async def get_or_compute(
        self,
        key: str,
        compute_fn: Callable,
        ttl: Optional[float] = None,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        value = await self.get(key)
        if value is not None:
            return value
        result = compute_fn(*args, **kwargs)
        if asyncio.iscoroutine(result):
            result = await result
        await self.set(key, result, ttl=ttl)
        return result

    async def stats(self) -> Dict[str, Any]:
        return {"l1": await self.l1.stats(), "l2": await self.l2.stats()}


# ── Cache Manager ─────────────────────────────────────────────────────────────

class CacheManager:
    """
    Named registry of AsyncCache instances.

    Usage::

        mgr = CacheManager()
        mgr.register("embeddings", AsyncCache(maxsize=2048, default_ttl=3600))

        await mgr.set("emb:hello", vector, backend="embeddings")
        vector = await mgr.get("emb:hello", backend="embeddings")
        print(await mgr.stats())
    """

    def __init__(self) -> None:
        self._caches: Dict[str, AsyncCache] = {
            "default": AsyncCache(maxsize=256, default_ttl=300),
        }

    def register(self, name: str, cache: AsyncCache) -> None:
        self._caches[name] = cache

    def get_cache(self, name: str = "default") -> AsyncCache:
        if name not in self._caches:
            self._caches[name] = AsyncCache()
        return self._caches[name]

    async def get(self, key: str, backend: str = "default") -> Optional[Any]:
        return await self.get_cache(backend).get(key)

    async def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[float] = None,
        backend: str = "default",
    ) -> None:
        await self.get_cache(backend).set(key, value, ttl=ttl)

    async def invalidate(self, key: str, backend: str = "default") -> bool:
        return await self.get_cache(backend).delete(key)

    async def clear_all(self) -> None:
        for cache in self._caches.values():
            await cache.clear()

    async def stats(self) -> Dict[str, Any]:
        return {name: await c.stats() for name, c in self._caches.items()}


# ── Utilities ─────────────────────────────────────────────────────────────────

def make_cache_key(*args: Any, **kwargs: Any) -> str:
    """Generate a stable 16-char hex cache key from arbitrary arguments."""
    try:
        raw = json.dumps({"args": args, "kwargs": kwargs}, sort_keys=True, default=str)
    except Exception:
        raw = str(args) + str(kwargs)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def cached(
    ttl: Optional[float] = None,
    maxsize: int = 128,
    key_fn: Optional[Callable[..., str]] = None,
) -> Callable:
    """
    Decorator that caches an async function's return value.

    Usage::

        @cached(ttl=60, maxsize=256)
        async def get_embedding(text: str) -> list:
            return await embed(text)

        # Access the underlying cache
        await get_embedding.cache.stats()
        await get_embedding.cache_clear()
    """
    _cache = AsyncCache(maxsize=maxsize, default_ttl=ttl)

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = key_fn(*args, **kwargs) if key_fn else make_cache_key(*args, **kwargs)
            return await _cache.get_or_compute(key, fn, ttl, *args, **kwargs)

        wrapper.cache = _cache  # type: ignore[attr-defined]
        wrapper.cache_clear = _cache.clear  # type: ignore[attr-defined]
        return wrapper

    return decorator


__all__ = [
    "AsyncCache",
    "CacheEntry",
    "CacheManager",
    "LRUCache",
    "MultiTierCache",
    "TTLCache",
    "cached",
    "make_cache_key",
]
