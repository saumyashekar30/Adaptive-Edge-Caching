"""Thread-safe Cache Store with pluggable eviction policies, capacity limits, and TTL management."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

try:
    from ..common.models import CacheEntry, CacheMetadata, CacheResult
    from .base import BaseEvictionPolicy
    from .lru import LRUPolicy
except ImportError:
    from src.common.models import CacheEntry, CacheMetadata, CacheResult  # type: ignore
    from src.cache.base import BaseEvictionPolicy  # type: ignore
    from src.cache.lru import LRUPolicy  # type: ignore

logger = logging.getLogger("cache_store")


class CacheStore:
    """Central edge caching storage engine."""

    def __init__(
        self,
        max_capacity_bytes: int = 50 * 1024 * 1024,  # 50 MB
        max_object_size_bytes: int = 5 * 1024 * 1024,  # 5 MB
        policy: Optional[BaseEvictionPolicy] = None,
        default_ttl_seconds: Optional[float] = None,
    ) -> None:
        self.max_capacity_bytes = max_capacity_bytes
        self.max_object_size_bytes = max_object_size_bytes
        self.policy: BaseEvictionPolicy = policy if policy is not None else LRUPolicy()
        self.default_ttl_seconds = default_ttl_seconds

        self._entries: Dict[str, CacheEntry] = {}
        self._current_size_bytes: int = 0
        self._lock = threading.RLock()

        # Telemetry counters
        self._hits: int = 0
        self._misses: int = 0
        self._evictions: int = 0
        self._expirations: int = 0

    @property
    def current_size_bytes(self) -> int:
        with self._lock:
            return self._current_size_bytes

    @property
    def object_count(self) -> int:
        with self._lock:
            return len(self._entries)

    def get(self, key: str) -> CacheResult:
        """Retrieve an entry from cache if present and unexpired."""
        start = time.perf_counter()
        with self._lock:
            if key not in self._entries:
                self._misses += 1
                elapsed = (time.perf_counter() - start) * 1000.0
                return CacheResult(status="MISS", entry=None, lookup_time_ms=elapsed)

            entry = self._entries[key]
            now = time.time()

            # TTL expiration check
            if entry.is_expired(now):
                logger.debug("Cache entry for key '%s' expired via TTL", key)
                self._expirations += 1
                self._delete_internal(key)
                self._misses += 1
                elapsed = (time.perf_counter() - start) * 1000.0
                return CacheResult(status="EXPIRED", entry=None, lookup_time_ms=elapsed)

            # Valid Hit
            entry.last_accessed_at = now
            entry.access_count += 1
            self._hits += 1
            self.policy.on_access(key, entry.to_metadata())

            elapsed = (time.perf_counter() - start) * 1000.0
            return CacheResult(status="HIT", entry=entry, lookup_time_ms=elapsed)

    def put(
        self,
        key: str,
        value: bytes,
        content_type: str = "application/octet-stream",
        version: str = "1.0",
        etag: str = "",
        ttl: Optional[float] = None,
        origin_latency_ms: float = 0.0,
        bandwidth_cost: float = 1.0,
        headers: Optional[Dict[str, str]] = None,
    ) -> bool:
        """Store or update an object in the cache.

        Enforces max_object_size_bytes and evicts victims if total capacity would be exceeded.
        """
        size = len(value)

        # 1. Reject if object is individually too large
        if size > self.max_object_size_bytes:
            logger.warning(
                "Rejected key '%s': size %d exceeds max object size limit %d",
                key, size, self.max_object_size_bytes
            )
            return False

        if size > self.max_capacity_bytes:
            logger.warning(
                "Rejected key '%s': size %d exceeds total cache capacity %d",
                key, size, self.max_capacity_bytes
            )
            return False

        resolved_ttl = ttl if ttl is not None else self.default_ttl_seconds

        with self._lock:
            # If replacing an existing key, free its space first
            if key in self._entries:
                self._current_size_bytes -= self._entries[key].size_bytes
                self.policy.on_evict(key)

            # 2. Evict until enough room is available
            needed_bytes = size
            while (self._current_size_bytes + needed_bytes) > self.max_capacity_bytes:
                candidates = {k: e.to_metadata() for k, e in self._entries.items() if k != key}
                if not candidates:
                    logger.error("Cache capacity full and no candidates available for eviction")
                    return False

                victim_key = self.policy.select_victim(candidates, needed_bytes=needed_bytes)
                if not victim_key or victim_key not in self._entries:
                    logger.error("Eviction policy failed to provide valid victim key")
                    return False

                logger.debug("Evicting key '%s' under policy '%s'", victim_key, self.policy.name)
                self._evictions += 1
                self._delete_internal(victim_key)

            # 3. Insert new entry
            entry = CacheEntry(
                key=key,
                value=value,
                size_bytes=size,
                content_type=content_type,
                version=version,
                etag=etag,
                ttl=resolved_ttl,
                origin_latency_ms=origin_latency_ms,
                bandwidth_cost=bandwidth_cost,
                headers=headers or {},
            )
            self._entries[key] = entry
            self._current_size_bytes += size
            self.policy.on_insert(key, entry.to_metadata())
            return True

    def delete(self, key: str) -> bool:
        """Delete an object from cache."""
        with self._lock:
            return self._delete_internal(key)

    def _delete_internal(self, key: str) -> bool:
        if key in self._entries:
            entry = self._entries.pop(key)
            self._current_size_bytes -= entry.size_bytes
            self.policy.on_evict(key)
            return True
        return False

    def invalidate(self, key: str) -> bool:
        """Invalidate a specific key."""
        return self.delete(key)

    def invalidate_by_prefix(self, prefix: str) -> int:
        """Invalidate all keys matching a prefix. Returns count of invalidated keys."""
        with self._lock:
            matching = [k for k in self._entries.keys() if k.startswith(prefix)]
            for k in matching:
                self._delete_internal(k)
            return len(matching)

    def clear(self) -> None:
        """Clear the entire cache."""
        with self._lock:
            self._entries.clear()
            self._current_size_bytes = 0
            self.policy.reset()

    def set_policy(self, policy: BaseEvictionPolicy) -> None:
        """Dynamically switch eviction policy."""
        with self._lock:
            self.policy = policy
            # Re-seed policy with current metadata
            for key, entry in self._entries.items():
                self.policy.on_insert(key, entry.to_metadata())

    def stats(self) -> Dict[str, Any]:
        """Return current performance and storage metrics."""
        with self._lock:
            total_requests = self._hits + self._misses
            hit_ratio = (self._hits / total_requests) if total_requests > 0 else 0.0
            return {
                "hits": self._hits,
                "misses": self._misses,
                "total_requests": total_requests,
                "hit_ratio": round(hit_ratio, 4),
                "evictions": self._evictions,
                "expirations": self._expirations,
                "object_count": len(self._entries),
                "current_size_bytes": self._current_size_bytes,
                "max_capacity_bytes": self.max_capacity_bytes,
                "utilization_pct": round((self._current_size_bytes / self.max_capacity_bytes) * 100, 2)
                if self.max_capacity_bytes > 0 else 0.0,
                "policy": self.policy.name,
            }
