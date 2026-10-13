"""Cache engine package for adaptive edge caching."""

from .base import BaseEvictionPolicy
from .store import CacheStore
from .lru import LRUPolicy
from .lfu import LFUPolicy
from .ttl import TTLPolicy
from .adaptive import AdaptivePolicy

__all__ = [
    "BaseEvictionPolicy",
    "CacheStore",
    "LRUPolicy",
    "LFUPolicy",
    "TTLPolicy",
    "AdaptivePolicy",
]
