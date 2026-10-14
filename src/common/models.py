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


@dataclass
class NodeMetrics:
    """Telemetry metrics collected from or reported by an edge node.
    
    Consumed by scoring engine: α·Latency + β·PacketLoss + γ·Utilization + δ·QueueingDelay.
    """
    node_id: str
    latency_ms: float = 0.0
    packet_loss_pct: float = 0.0
    utilization_pct: float = 0.0
    queueing_delay_ms: float = 0.0
    hit_ratio: float = 0.0
    active_connections: int = 0
    total_requests: int = 0
    uptime: float = 0.0
    status: str = "HEALTHY"  # "HEALTHY", "DEGRADED", "UNHEALTHY", "OFFLINE"
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "latency_ms": round(self.latency_ms, 3),
            "packet_loss_pct": round(self.packet_loss_pct, 3),
            "utilization_pct": round(self.utilization_pct, 3),
            "queueing_delay_ms": round(self.queueing_delay_ms, 3),
            "hit_ratio": round(self.hit_ratio, 4),
            "active_connections": self.active_connections,
            "total_requests": self.total_requests,
            "uptime": round(self.uptime, 2),
            "status": self.status,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> NodeMetrics:
        return cls(
            node_id=str(data.get("node_id", "unknown")),
            latency_ms=float(data.get("latency_ms", 0.0)),
            packet_loss_pct=float(data.get("packet_loss_pct", 0.0)),
            utilization_pct=float(data.get("utilization_pct", 0.0)),
            queueing_delay_ms=float(data.get("queueing_delay_ms", 0.0)),
            hit_ratio=float(data.get("hit_ratio", 0.0)),
            active_connections=int(data.get("active_connections", 0)),
            total_requests=int(data.get("total_requests", 0)),
            uptime=float(data.get("uptime", 0.0)),
            status=str(data.get("status", "HEALTHY")).upper(),
            timestamp=float(data.get("timestamp", time.time())),
        )


@dataclass
class RoutingWeights:
    """Weights governing edge node selection penalty score calculation."""
    alpha: float = 0.40  # Latency (ms) weight
    beta: float = 0.30   # Packet Loss (%) weight
    gamma: float = 0.20  # Utilization (%) weight
    delta: float = 0.10  # Queueing delay (ms) weight

    def to_dict(self) -> Dict[str, float]:
        return {
            "alpha": self.alpha,
            "beta": self.beta,
            "gamma": self.gamma,
            "delta": self.delta,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RoutingWeights:
        return cls(
            alpha=float(data.get("alpha", 0.40)),
            beta=float(data.get("beta", 0.30)),
            gamma=float(data.get("gamma", 0.20)),
            delta=float(data.get("delta", 0.10)),
        )


@dataclass
class NodeScore:
    """Routing utility/penalty score computed for an edge node."""
    node_id: str
    score: float
    breakdown: Dict[str, float] = field(default_factory=dict)
    is_eligible: bool = True
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "score": round(self.score, 4) if self.score != float("inf") else "inf",
            "breakdown": {k: round(v, 4) for k, v in self.breakdown.items()},
            "is_eligible": self.is_eligible,
            "timestamp": self.timestamp,
        }


@dataclass
class HealthCheckResult:
    """Result of an HTTP liveness/readiness probe to an edge node."""
    node_id: str
    url: str
    status: str  # "HEALTHY", "DEGRADED", "UNHEALTHY", "OFFLINE"
    status_code: int = 0
    rtt_ms: float = 0.0
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class RecoveryRecord:
    """Telemetry log tracking recovery from failure."""
    node_id: str
    failed_at: float
    recovered_at: float
    recovery_time_seconds: float
    consecutive_failures: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "failed_at": self.failed_at,
            "recovered_at": self.recovered_at,
            "recovery_time_seconds": round(self.recovery_time_seconds, 3),
            "consecutive_failures": self.consecutive_failures,
        }

