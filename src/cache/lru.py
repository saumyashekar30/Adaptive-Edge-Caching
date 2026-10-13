"""Least Recently Used (LRU) cache eviction policy."""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Mapping, Optional

try:
    from ..common.models import CacheMetadata
    from .base import BaseEvictionPolicy
except ImportError:
    from src.common.models import CacheMetadata  # type: ignore
    from src.cache.base import BaseEvictionPolicy  # type: ignore


class LRUPolicy(BaseEvictionPolicy):
    """Evicts the least recently accessed item first."""

    def __init__(self) -> None:
        # OrderedDict maintains insertion/access order: oldest at front, newest at back
        self._order: OrderedDict[str, float] = OrderedDict()

    @property
    def name(self) -> str:
        return "lru"

    def on_insert(self, key: str, metadata: CacheMetadata) -> None:
        self._order[key] = metadata.last_accessed_at
        self._order.move_to_end(key, last=True)

    def on_access(self, key: str, metadata: CacheMetadata) -> None:
        self._order[key] = metadata.last_accessed_at
        self._order.move_to_end(key, last=True)

    def on_evict(self, key: str) -> None:
        self._order.pop(key, None)

    def select_victim(
        self,
        candidates: Mapping[str, CacheMetadata],
        needed_bytes: int = 0
    ) -> Optional[str]:
        # Walk from oldest accessed in _order that is present in candidates
        for key in self._order:
            if key in candidates:
                return key

        # Fallback to candidate timestamps if not tracked
        if candidates:
            return min(candidates.keys(), key=lambda k: candidates[k].last_accessed_at)
        return None

    def reset(self) -> None:
        self._order.clear()
