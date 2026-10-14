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
