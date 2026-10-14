"""Eviction policy interface for edge caching.

This is the primary seam agreed upon in Week 1 between Immanuel (Cache/Proxy)
and Riddhi (Edge node runtime).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Mapping, Optional

try:
    from ..common.models import CacheMetadata
except ImportError:
    from src.common.models import CacheMetadata  # type: ignore


class BaseEvictionPolicy(ABC):
    """Abstract interface defining cache eviction policies.

    Implementations include LRU, LFU, TTL, and Adaptive policies.
    The store delegates candidate eviction decisions and state transitions
    to this policy.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the eviction policy (e.g., 'lru', 'lfu', 'ttl', 'adaptive')."""
        pass

    @abstractmethod
    def on_insert(self, key: str, metadata: CacheMetadata) -> None:
        """Invoked when a new key is added or updated in the cache store."""
        pass

    @abstractmethod
    def on_access(self, key: str, metadata: CacheMetadata) -> None:
        """Invoked on every cache hit to update recency, frequency, etc."""
        pass

    @abstractmethod
    def on_evict(self, key: str) -> None:
        """Invoked when a key has been removed from the cache store."""
        pass

    @abstractmethod
    def select_victim(
        self,
        candidates: Mapping[str, CacheMetadata],
        needed_bytes: int = 0
    ) -> Optional[str]:
        """Select a single candidate key for eviction based on the policy algorithm.

        Args:
            candidates: Mapping from key to CacheMetadata for all eviction candidates.
            needed_bytes: Optional size in bytes required to accommodate incoming object.

        Returns:
            The key of the victim selected for eviction, or None if no candidate exists.
        """
        pass

    def reset(self) -> None:
        """Optional hook to clear internal tracking structures."""
        pass
