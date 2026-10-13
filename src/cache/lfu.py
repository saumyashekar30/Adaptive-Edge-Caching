"""Least Frequently Used (LFU) cache eviction policy."""

from __future__ import annotations

from typing import Dict, Mapping, Optional

try:
    from ..common.models import CacheMetadata
    from .base import BaseEvictionPolicy
except ImportError:
    from src.common.models import CacheMetadata  # type: ignore
    from src.cache.base import BaseEvictionPolicy  # type: ignore


class LFUPolicy(BaseEvictionPolicy):
    """Evicts the least frequently accessed item first.

    Ties are broken using least-recent access time.
    """

    def __init__(self) -> None:
        self._frequencies: Dict[str, int] = {}
        self._last_accessed: Dict[str, float] = {}

    @property
    def name(self) -> str:
        return "lfu"

    def on_insert(self, key: str, metadata: CacheMetadata) -> None:
        self._frequencies[key] = metadata.access_count
        self._last_accessed[key] = metadata.last_accessed_at

    def on_access(self, key: str, metadata: CacheMetadata) -> None:
        self._frequencies[key] = metadata.access_count
        self._last_accessed[key] = metadata.last_accessed_at

    def on_evict(self, key: str) -> None:
        self._frequencies.pop(key, None)
        self._last_accessed.pop(key, None)

    def select_victim(
        self,
        candidates: Mapping[str, CacheMetadata],
        needed_bytes: int = 0
    ) -> Optional[str]:
        if not candidates:
            return None

        # Find key with lowest frequency; tie-break on oldest last_accessed_at
        min_key: Optional[str] = None
        min_freq = float("inf")
        min_time = float("inf")

        for key, meta in candidates.items():
            freq = self._frequencies.get(key, meta.access_count)
            t = self._last_accessed.get(key, meta.last_accessed_at)

            if freq < min_freq or (freq == min_freq and t < min_time):
                min_freq = freq
                min_time = t
                min_key = key

        return min_key

    def reset(self) -> None:
        self._frequencies.clear()
        self._last_accessed.clear()
