"""Shared data structures and models across all modules."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


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


# ---------------------------------------------------------------------------
# Edge node configuration, identity, and experiment/workload records.
# (Riddhi: Distributed Edge Runtime / Experiment Automation)
# ---------------------------------------------------------------------------


@dataclass
class NodeConfig:
    """Validated configuration/identity for one independently runnable edge node.

    Loaded from a `config/nodes/*.yaml` file by `src.common.config.load_node_config`.
    """
    node_id: str
    host: str = "0.0.0.0"
    port: int = 8080
    capacity_mb: float = 50.0
    max_object_size_mb: float = 5.0
    policy: str = "lru"  # "lru" | "lfu" | "ttl" | "adaptive"
    origin_url: str = "http://127.0.0.1:8000"
    peers: List[str] = field(default_factory=list)
    controller_url: Optional[str] = None
    telemetry_interval_seconds: float = 5.0
    default_ttl_seconds: Optional[float] = None
    # Address other nodes/the controller should use to reach this node, when
    # it differs from the bind host (e.g. binding 0.0.0.0 inside a Docker
    # container but reachable as the container's service name "node1").
    advertise_host: Optional[str] = None

    @property
    def capacity_bytes(self) -> int:
        return int(self.capacity_mb * 1024 * 1024)

    @property
    def max_object_size_bytes(self) -> int:
        return int(self.max_object_size_mb * 1024 * 1024)

    @property
    def base_url(self) -> str:
        if self.advertise_host:
            host = self.advertise_host
        elif self.host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
        else:
            host = self.host
        return f"http://{host}:{self.port}"


class RequestOutcome(str, Enum):
    """Distinct terminal outcomes of a single client request, used by workload
    generation, experiment recording, and analysis. Never collapse PEER_HIT
    into LOCAL_HIT -- the API contract (docs/api_contracts.md Sec.1/5) and the
    proxy's own `X-Cache` header keep them distinct."""
    LOCAL_HIT = "local_hit"
    PEER_HIT = "peer_hit"
    ORIGIN_FETCH = "origin_fetch"
    BYPASS = "bypass"
    ERROR = "error"
    TIMEOUT = "timeout"

    @classmethod
    def from_headers(cls, status_code: int, headers: Dict[str, str]) -> "RequestOutcome":
        """Classify a completed HTTP response using the proxy's own response headers."""
        if status_code >= 500 or status_code == 0:
            return cls.ERROR
        x_cache = (headers.get("X-Cache") or headers.get("x-cache") or "").upper()
        if x_cache == "HIT":
            return cls.LOCAL_HIT
        if x_cache == "PEER_HIT":
            return cls.PEER_HIT
        if x_cache == "BYPASS":
            return cls.BYPASS
        if x_cache == "MISS":
            return cls.ORIGIN_FETCH
        if status_code >= 400:
            return cls.ERROR
        return cls.ORIGIN_FETCH


@dataclass
class RequestRecord:
    """Raw, per-request measurement produced by the workload generator and
    consumed by the experiment runner / analyzer. One record per attempted
    HTTP request -- failed and timed-out requests are kept, not discarded."""
    experiment_id: str
    request_id: int
    timestamp: float
    target_url: str
    resource_id: str
    status_code: int
    outcome: str  # RequestOutcome value
    latency_ms: float
    node_id: str = ""
    error: Optional[str] = None
    is_warmup: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "request_id": self.request_id,
            "timestamp": self.timestamp,
            "target_url": self.target_url,
            "resource_id": self.resource_id,
            "status_code": self.status_code,
            "outcome": self.outcome,
            "latency_ms": round(self.latency_ms, 4),
            "node_id": self.node_id,
            "error": self.error,
            "is_warmup": self.is_warmup,
        }


@dataclass
class WorkloadConfig:
    """Configuration for a single reproducible request workload."""
    distribution: str = "zipf"  # "zipf" | "uniform" | "bursty"
    num_requests: int = 500
    unique_objects: int = 100
    zipf_alpha: float = 1.1
    concurrency: int = 4
    request_rate_per_sec: Optional[float] = None
    burst_size: int = 20
    burst_idle_seconds: float = 1.0
    seed: int = 42
    warmup_requests: int = 0
    request_timeout_seconds: float = 5.0
    object_size_mean_bytes: int = 4096
    object_size_std_bytes: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class ExperimentConfig:
    """Top-level reproducible experiment definition, loaded from
    `config/experiments/*.yaml` by `src.common.config.load_experiment_config`."""
    experiment_name: str
    duration_seconds: float = 60.0
    num_clients: int = 10
    workload: WorkloadConfig = field(default_factory=WorkloadConfig)
    cache_capacity_mb: float = 2.0
    cache_max_object_size_kb: float = 64.0
    policies: List[str] = field(default_factory=lambda: ["lru"])
    origin_base_latency_ms: float = 60.0
    origin_jitter_ms: float = 10.0
    repetitions: int = 1
    mode: str = "self_managed"  # "self_managed" | "external"
    target_url: Optional[str] = None  # required when mode == "external"
    raw: Dict[str, Any] = field(default_factory=dict)  # original parsed YAML, for provenance


@dataclass
class ExperimentSummary:
    """Machine-readable aggregate summary for one (experiment, policy, repetition) run."""
    experiment_id: str
    experiment_name: str
    policy: str
    repetition: int
    total_requests: int
    successful_requests: int
    failed_requests: int
    timed_out_requests: int
    local_hits: int
    peer_hits: int
    origin_fetches: int
    bypasses: int
    cache_hit_ratio: Optional[float]
    mean_latency_ms: Optional[float]
    median_latency_ms: Optional[float]
    p95_latency_ms: Optional[float]
    p99_latency_ms: Optional[float]
    throughput_rps: Optional[float]
    error_rate: Optional[float]
    timeout_rate: Optional[float]
    duration_seconds: float
    started_at: float
    finished_at: float

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)

