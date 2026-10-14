"""Time-To-Live (TTL) cache eviction policy."""

from __future__ import annotations

import time
from typing import Mapping, Optional

try:
    from ..common.models import CacheMetadata
    from .base import BaseEvictionPolicy
except ImportError:
    from src.common.models import CacheMetadata  # type: ignore
    from src.cache.base import BaseEvictionPolicy  # type: ignore


class TTLPolicy(BaseEvictionPolicy):
    """Evicts items closest to expiration (or already expired) first.

    For items without explicit TTL, falls back to the oldest created entry.
    """

    @property
    def name(self) -> str:
        return "ttl"

    def on_insert(self, key: str, metadata: CacheMetadata) -> None:
        pass

    def on_access(self, key: str, metadata: CacheMetadata) -> None:
        pass

    def on_evict(self, key: str) -> None:
        pass

    def select_victim(
        self,
        candidates: Mapping[str, CacheMetadata],
        needed_bytes: int = 0
    ) -> Optional[str]:
        if not candidates:
            return None

        now = time.time()

        # 1. Any candidate already expired?
        for key, meta in candidates.items():
            if meta.ttl is not None and (now - meta.created_at) >= meta.ttl:
                return key

        # 2. Candidate with nearest expiry deadline
        min_key: Optional[str] = None
        min_remaining = float("inf")

        for key, meta in candidates.items():
            if meta.ttl is not None:
                remaining = meta.created_at + meta.ttl - now
            else:
                # Infinite/no TTL: treat remaining as very large, sort by age
                remaining = float("inf")

            if remaining < min_remaining:
                min_remaining = remaining
                min_key = key

        # If all candidates have no TTL, evict the oldest created
        if min_key is None or min_remaining == float("inf"):
            return min(candidates.keys(), key=lambda k: candidates[k].created_at)

        return min_key

    def reset(self) -> None:
        pass
