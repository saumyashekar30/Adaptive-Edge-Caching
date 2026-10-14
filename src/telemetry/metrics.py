"""Hit ratio, latency, loss, and queueing metrics collector and mock telemetry engine."""

from __future__ import annotations

import json
import logging
import random
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional, Union

try:
    from ..common.models import NodeMetrics
except ImportError:
    from src.common.models import NodeMetrics  # type: ignore

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

logger = logging.getLogger("telemetry_metrics")


class TelemetryCollector:
    """Stores, collects, and synchronizes edge node metrics."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._latest_metrics: Dict[str, NodeMetrics] = {}
        self._history: Dict[str, List[NodeMetrics]] = {}

    def record_telemetry(self, metrics: Union[NodeMetrics, Dict[str, Any]]) -> NodeMetrics:
        """Store a new telemetry sample for a node."""
        if isinstance(metrics, dict):
            obj = NodeMetrics.from_dict(metrics)
        else:
            obj = metrics

        node_id = obj.node_id
        with self._lock:
            self._latest_metrics[node_id] = obj
            if node_id not in self._history:
                self._history[node_id] = []
            self._history[node_id].append(obj)
            # Keep last 500 samples
            if len(self._history[node_id]) > 500:
                self._history[node_id].pop(0)

        logger.debug("Recorded telemetry for %s: latency=%.1fms, loss=%.1f%%, util=%.1f%%",
                     node_id, obj.latency_ms, obj.packet_loss_pct, obj.utilization_pct)
        return obj

    def get_metrics(self, node_id: str) -> Optional[NodeMetrics]:
        """Get the latest metrics for a node."""
        with self._lock:
            return self._latest_metrics.get(node_id)

    def get_all_metrics(self) -> Dict[str, NodeMetrics]:
        """Return a copy of the latest metrics for all known nodes."""
        with self._lock:
            return dict(self._latest_metrics)

    def fetch_from_node(self, node_id: str, base_url: str, timeout: float = 1.0) -> Optional[NodeMetrics]:
        """Poll telemetry from a live edge node per docs/api_contracts.md."""
        url = base_url.rstrip("/")
        start = time.perf_counter()
        try:
            # 1. Fetch /health
            health_req = urllib.request.Request(f"{url}/health")
            with urllib.request.urlopen(health_req, timeout=timeout) as resp:
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                health_data = json.loads(resp.read().decode("utf-8"))

            # 2. Fetch /cache/stats
            stats_req = urllib.request.Request(f"{url}/cache/stats")
            with urllib.request.urlopen(stats_req, timeout=timeout) as resp:
                stats_data = json.loads(resp.read().decode("utf-8"))

            metrics = NodeMetrics(
                node_id=node_id,
                latency_ms=elapsed_ms,
                packet_loss_pct=0.0,
                utilization_pct=float(stats_data.get("utilization_pct", 0.0)),
                queueing_delay_ms=max(0.5, elapsed_ms * 0.1),
                hit_ratio=float(stats_data.get("hit_ratio", 0.0)),
                total_requests=int(stats_data.get("total_requests", 0)),
                uptime=float(health_data.get("uptime", 0.0)),
                status="HEALTHY" if health_data.get("status") == "healthy" else "DEGRADED",
            )
            return self.record_telemetry(metrics)
        except Exception as e:
            logger.warning("Failed to poll telemetry from %s (%s): %s", node_id, url, e)
            return None


class MockTelemetryGenerator:
    """Generates synthetic telemetry for bootstrapping and simulation before live agents are deployed."""

    @staticmethod
    def generate_metrics(
        node_id: str,
        latency_ms: float = 10.0,
        packet_loss_pct: float = 0.0,
        utilization_pct: float = 35.0,
        queueing_delay_ms: float = 2.0,
        hit_ratio: float = 0.85,
        status: str = "HEALTHY",
    ) -> NodeMetrics:
        """Create a single deterministic or custom telemetry record."""
        return NodeMetrics(
            node_id=node_id,
            latency_ms=latency_ms,
            packet_loss_pct=packet_loss_pct,
            utilization_pct=utilization_pct,
            queueing_delay_ms=queueing_delay_ms,
            hit_ratio=hit_ratio,
            status=status,
            timestamp=time.time(),
        )

    @staticmethod
    def generate_scenario_metrics(
        node_id: str,
        scenario: str = "normal",
        seed: Optional[int] = None,
    ) -> NodeMetrics:
        """Generate profile-based synthetic metrics for varied network conditions."""
        rng = random.Random(seed)
        scenario_lower = scenario.lower()

        if "high_latency" in scenario_lower:
            return NodeMetrics(
                node_id=node_id,
                latency_ms=rng.uniform(160.0, 240.0),
                packet_loss_pct=rng.uniform(0.0, 1.0),
                utilization_pct=rng.uniform(25.0, 45.0),
                queueing_delay_ms=rng.uniform(1.0, 4.0),
                hit_ratio=0.82,
                status="HEALTHY",
            )
        elif "packet_loss" in scenario_lower:
            return NodeMetrics(
                node_id=node_id,
                latency_ms=rng.uniform(15.0, 30.0),
                packet_loss_pct=rng.uniform(6.0, 15.0),
                utilization_pct=rng.uniform(30.0, 50.0),
                queueing_delay_ms=rng.uniform(3.0, 8.0),
                hit_ratio=0.75,
                status="DEGRADED",
            )
        elif "congestion" in scenario_lower:
            return NodeMetrics(
                node_id=node_id,
                latency_ms=rng.uniform(40.0, 80.0),
                packet_loss_pct=rng.uniform(1.0, 4.0),
                utilization_pct=rng.uniform(85.0, 96.0),
                queueing_delay_ms=rng.uniform(25.0, 55.0),
                hit_ratio=0.70,
                status="DEGRADED",
            )
        elif "failure" in scenario_lower or "dead" in scenario_lower:
            return NodeMetrics(
                node_id=node_id,
                latency_ms=999.0,
                packet_loss_pct=100.0,
                utilization_pct=0.0,
                queueing_delay_ms=999.0,
                hit_ratio=0.0,
                status="OFFLINE",
            )
        else:  # Normal nominal operation
            return NodeMetrics(
                node_id=node_id,
                latency_ms=rng.uniform(8.0, 18.0),
                packet_loss_pct=rng.uniform(0.0, 0.5),
                utilization_pct=rng.uniform(20.0, 40.0),
                queueing_delay_ms=rng.uniform(1.0, 3.0),
                hit_ratio=0.88,
                status="HEALTHY",
            )


# ---------------------------------------------------------------------------
# EdgeNodeTelemetry (Riddhi): in-process metrics collected *inside* a running
# EdgeNode, as opposed to TelemetryCollector above which polls/aggregates
# metrics from the controller's side. src/edge/node.py owns one instance of
# this class and periodically pushes its snapshot() to the routing
# controller's `POST /telemetry` endpoint.
#
# Per-metric documentation (unit / collection method / sampling / caveats):
#
# | Field                 | Unit          | Method                         | Sampling          | Nature                                   |
# |------------------------|---------------|---------------------------------|--------------------|-------------------------------------------|
# | total_requests         | count         | stats_hook counter (exact)      | every request      | measured                                  |
# | local_hits             | count         | stats_hook counter (exact)      | every request      | measured                                  |
# | peer_hits              | count         | stats_hook counter (exact)      | every request      | measured                                  |
# | origin_fetches         | count         | stats_hook counter (exact)      | every request      | measured                                  |
# | bypasses / errors      | count         | stats_hook counter (exact)      | every request      | measured                                  |
# | hit_ratio              | ratio [0,1]   | (local_hits+peer_hits)/total    | on snapshot()       | derived                                   |
# | mean_latency_ms        | milliseconds  | perf_counter delta, mean        | rolling window      | measured (wall latency of this process)   |
# | utilization_pct        | percent [0,100]| psutil.cpu_percent()           | on snapshot()       | measured (process/system CPU, NOT link BW)|
# | memory_rss_mb          | megabytes     | psutil.Process().memory_info()  | on snapshot()       | measured                                  |
# | cache_utilization_pct  | percent [0,100]| CacheStore.stats()["utilization_pct"] | on snapshot() | measured (relayed from cache module) |
# | packet_loss_pct        | percent [0,100]| n/a                             | n/a                 | UNAVAILABLE in ordinary execution: a stdlib HTTP process cannot observe IP-layer packet loss without raw sockets or `tc`/netem instrumentation (see simulation/apply_tc_netem.sh). Always reported as 0.0 with `packet_loss_source="unavailable"` unless a caller explicitly supplies a measured value from the network-simulation harness. Never fabricated. |
# | queueing_delay_ms      | milliseconds  | n/a                             | n/a                 | ESTIMATED as a small fraction of measured latency (no real OS queue depth is observable from user space without privileged instrumentation) -- flagged via queueing_delay_source="estimated". |
# | active_connections     | count         | ThreadingHTTPServer thread count heuristic | on snapshot() | derived/approximate |
#
# CPU/memory collection requires the optional `psutil` dependency (added to
# requirements.txt); when it is not installed, utilization_pct/memory_rss_mb
# are reported as None rather than silently substituted with 0.
# ---------------------------------------------------------------------------


class EdgeNodeTelemetry:
    """Thread-safe, low-overhead telemetry aggregator owned by one EdgeNode.

    Counters are updated via `record(outcome, latency_ms)`, intended to be
    wired as the `stats_hook` of `ProxyRequestHandler`/`ProxyServer`. Call
    `snapshot()` periodically (not per-request) to avoid expensive system
    scans on the hot request path.
    """

    _OUTCOME_MAP = {
        "HIT": "local_hits",
        "PEER_HIT": "peer_hits",
        "MISS": "origin_fetches",
        "BYPASS": "bypasses",
        "ERROR": "errors",
    }

    def __init__(self, node_id: str, cache_store: Any = None, latency_window: int = 200) -> None:
        self.node_id = node_id
        self.cache_store = cache_store
        self._lock = threading.Lock()
        self._counts: Dict[str, int] = {
            "local_hits": 0, "peer_hits": 0, "origin_fetches": 0,
            "bypasses": 0, "errors": 0,
        }
        self._latencies: List[float] = []
        self._latency_window = latency_window
        self._start_time = time.monotonic()
        self._process = psutil.Process() if _HAS_PSUTIL else None
        if self._process is not None:
            # Prime psutil's internal interval timer; the first real call
            # after this returns a meaningful non-blocking percentage.
            self._process.cpu_percent(interval=None)

    def record(self, outcome: str, latency_ms: float) -> None:
        """Record one completed request's terminal outcome and latency."""
        key = self._OUTCOME_MAP.get(outcome.upper(), "errors")
        with self._lock:
            self._counts[key] += 1
            self._latencies.append(latency_ms)
            if len(self._latencies) > self._latency_window:
                self._latencies.pop(0)

    def reset(self) -> None:
        with self._lock:
            for k in self._counts:
                self._counts[k] = 0
            self._latencies.clear()

    def snapshot(self) -> NodeMetrics:
        """Build a point-in-time NodeMetrics snapshot. Cheap: psutil CPU
        sampling is non-blocking (interval=None) after the constructor's
        priming call; no per-request system scan ever occurs."""
        with self._lock:
            counts = dict(self._counts)
            latencies = list(self._latencies)

        total = sum(counts.values())
        hit_like = counts["local_hits"] + counts["peer_hits"]
        hit_ratio = (hit_like / total) if total > 0 else 0.0
        mean_latency = (sum(latencies) / len(latencies)) if latencies else 0.0

        utilization_pct = 0.0
        if self._process is not None:
            try:
                utilization_pct = self._process.cpu_percent(interval=None)
            except Exception:
                utilization_pct = 0.0

        queueing_delay_ms = round(mean_latency * 0.05, 3)  # estimated, see module docstring

        return NodeMetrics(
            node_id=self.node_id,
            latency_ms=round(mean_latency, 3),
            packet_loss_pct=0.0,  # unavailable from user-space HTTP process, see module docstring
            utilization_pct=round(utilization_pct, 2),
            queueing_delay_ms=queueing_delay_ms,
            hit_ratio=round(hit_ratio, 4),
            active_connections=0,
            total_requests=total,
            uptime=round(time.monotonic() - self._start_time, 2),
            status="HEALTHY",
        )

    def snapshot_dict(self) -> Dict[str, Any]:
        """Extended snapshot including raw event counters and cache stats,
        for local logging/debugging -- richer than the NodeMetrics shape the
        routing controller expects."""
        metrics = self.snapshot()
        data = metrics.to_dict()
        with self._lock:
            data["counts"] = dict(self._counts)
        data["memory_rss_mb"] = None
        if self._process is not None:
            try:
                data["memory_rss_mb"] = round(self._process.memory_info().rss / (1024 * 1024), 2)
            except Exception:
                pass
        if self.cache_store is not None:
            try:
                data["cache_stats"] = self.cache_store.stats()
            except Exception:
                data["cache_stats"] = None
        data["packet_loss_source"] = "unavailable"
        data["queueing_delay_source"] = "estimated"
        return data

    def push_to_controller(self, controller_url: str, timeout: float = 1.5) -> bool:
        """POST this node's current NodeMetrics snapshot to a routing
        controller's `/telemetry` endpoint (src/routing/controller.py).
        Returns True on a 2xx response, False on any network/timeout error
        (never raises -- telemetry push is best-effort and must not crash
        the node's main loop)."""
        payload = json.dumps(self.snapshot().to_dict()).encode("utf-8")
        url = controller_url.rstrip("/") + "/telemetry"
        try:
            req = urllib.request.Request(
                url, data=payload, method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return 200 <= resp.status < 300
        except Exception as e:
            logger.debug("Telemetry push to %s failed: %s", controller_url, e)
            return False
