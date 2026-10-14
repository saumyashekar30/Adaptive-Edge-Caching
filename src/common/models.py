"""Shared data structures and models across all modules."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class CacheMetadata:
    """Metadata representing an item in the cache, used for scoring and eviction."""
    key: str
    size_bytes: int
    version: str = "1.0"
    etag: str = ""
    created_at: float = field(default_factory=time.time)
    last_accessed_at: float = field(default_factory=time.time)
    access_count: int = 1
    ttl: Optional[float] = None  # None indicates no explicit expiration
    origin_latency_ms: float = 0.0
    bandwidth_cost: float = 1.0


@dataclass
class CacheEntry:
    """A cached object including raw bytes payload and metadata."""
    key: str
    value: bytes
    size_bytes: int = 0
    content_type: str = "application/octet-stream"
    version: str = "1.0"
    etag: str = ""
    created_at: float = field(default_factory=time.time)
    last_accessed_at: float = field(default_factory=time.time)
    access_count: int = 1
    ttl: Optional[float] = None
    origin_latency_ms: float = 0.0
    bandwidth_cost: float = 1.0
    headers: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.size_bytes == 0 and self.value is not None:
            self.size_bytes = len(self.value)

    def is_expired(self, current_time: Optional[float] = None) -> bool:
        """Check if entry has passed its TTL."""
        if self.ttl is None or self.ttl <= 0:
            return False
        now = current_time if current_time is not None else time.time()
        return (now - self.created_at) >= self.ttl

    def to_metadata(self) -> CacheMetadata:
        """Extract lightweight metadata suitable for eviction policy decision."""
        return CacheMetadata(
            key=self.key,
            size_bytes=self.size_bytes,
            version=self.version,
            etag=self.etag,
            created_at=self.created_at,
            last_accessed_at=self.last_accessed_at,
            access_count=self.access_count,
            ttl=self.ttl,
            origin_latency_ms=self.origin_latency_ms,
            bandwidth_cost=self.bandwidth_cost,
        )


@dataclass
class CacheResult:
    """Result of a cache query."""
    status: str  # "HIT", "MISS", "EXPIRED", "BYPASS"
    entry: Optional[CacheEntry] = None
    lookup_time_ms: float = 0.0


@dataclass
class OriginResponse:
    """Normalized response received from the origin server."""
    status_code: int
    body: bytes
    headers: Dict[str, str] = field(default_factory=dict)
    latency_ms: float = 0.0
    version: str = "1.0"
    etag: str = ""
    content_type: str = "application/octet-stream"


@dataclass
class InvalidationRecord:
    """Record of an invalidation event for consistency tracking."""
    resource_id: str
    invalidated_at: float = field(default_factory=time.time)
    new_version: Optional[str] = None
    stale_hits: int = 0
    convergence_time_ms: float = 0.0
    status: str = "PENDING"  # "PENDING", "CONVERGED", "EXPIRED"
    metadata: Dict[str, Any] = field(default_factory=dict)
