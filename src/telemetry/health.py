"""Health check endpoint and probe handlers marking silent nodes unavailable."""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from ..common.models import HealthCheckResult
except ImportError:
    from src.common.models import HealthCheckResult  # type: ignore

logger = logging.getLogger("health_checker")


class HealthChecker:
    """Monitors node reachability and liveness via HTTP GET /health probes."""

    def __init__(
        self,
        timeout_seconds: float = 0.8,
        failure_threshold: int = 2,
        recovery_threshold: int = 2,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.failure_threshold = failure_threshold
        self.recovery_threshold = recovery_threshold

        self._lock = threading.RLock()
        self._nodes: Dict[str, str] = {}  # node_id -> base_url
        self._node_status: Dict[str, str] = {}  # node_id -> "HEALTHY" | "DEGRADED" | "OFFLINE"
        self._consecutive_failures: Dict[str, int] = {}
        self._consecutive_successes: Dict[str, int] = {}
        self._last_results: Dict[str, HealthCheckResult] = {}
        self._listeners: List[Callable[[str, str, str], None]] = []  # (node_id, old_status, new_status)

    def register_node(self, node_id: str, url: str) -> None:
        """Register a node URL for health checking."""
        with self._lock:
            clean_url = url.rstrip("/")
            self._nodes[node_id] = clean_url
            if node_id not in self._node_status:
                self._node_status[node_id] = "HEALTHY"
                self._consecutive_failures[node_id] = 0
                self._consecutive_successes[node_id] = self.recovery_threshold

    def deregister_node(self, node_id: str) -> None:
        """Remove a node from health checking."""
        with self._lock:
            self._nodes.pop(node_id, None)
            self._node_status.pop(node_id, None)
            self._consecutive_failures.pop(node_id, None)
            self._consecutive_successes.pop(node_id, None)
            self._last_results.pop(node_id, None)

    def add_listener(self, listener: Callable[[str, str, str], None]) -> None:
        """Add callback invoked on node status transition: listener(node_id, old_status, new_status)."""
        with self._lock:
            self._listeners.append(listener)

    def probe_node(self, node_id: str, url: Optional[str] = None) -> HealthCheckResult:
        """Execute a single HTTP probe against an edge node's /health endpoint."""
        target_url = url or self._nodes.get(node_id)
        if not target_url:
            return HealthCheckResult(
                node_id=node_id,
                url="",
                status="OFFLINE",
                error="Node URL not registered",
            )

        probe_url = f"{target_url.rstrip('/')}/health"
        start_time = time.perf_counter()

        try:
            req = urllib.request.Request(probe_url, headers={"User-Agent": "AdaptiveEdge-HealthCheck/1.0"})
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                status_code = resp.status
                body = resp.read()
                try:
                    payload = json.loads(body.decode("utf-8"))
                    is_healthy = payload.get("status") == "healthy"
                except Exception:
                    is_healthy = status_code == 200

                if status_code == 200 and is_healthy:
                    result = HealthCheckResult(
                        node_id=node_id,
                        url=target_url,
                        status="HEALTHY",
                        status_code=status_code,
                        rtt_ms=elapsed_ms,
                    )
                else:
                    result = HealthCheckResult(
                        node_id=node_id,
                        url=target_url,
                        status="DEGRADED",
                        status_code=status_code,
                        rtt_ms=elapsed_ms,
                        error=f"Unhealthy status response: {body.decode('utf-8', errors='ignore')}",
                    )
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            result = HealthCheckResult(
                node_id=node_id,
                url=target_url,
                status="OFFLINE",
                rtt_ms=elapsed_ms,
                error=str(e),
            )

        self._record_probe_result(result)
        return result

    def _record_probe_result(self, result: HealthCheckResult) -> None:
        """Update node status state machine based on probe outcome."""
        node_id = result.node_id
        old_status = "HEALTHY"
        new_status = old_status

        with self._lock:
            old_status = self._node_status.get(node_id, "HEALTHY")
            self._last_results[node_id] = result

            if result.status == "HEALTHY":
                self._consecutive_successes[node_id] = self._consecutive_successes.get(node_id, 0) + 1
                self._consecutive_failures[node_id] = 0

                if self._consecutive_successes[node_id] >= self.recovery_threshold:
                    new_status = "HEALTHY"
                else:
                    new_status = old_status
            else:
                self._consecutive_failures[node_id] = self._consecutive_failures.get(node_id, 0) + 1
                self._consecutive_successes[node_id] = 0

                if self._consecutive_failures[node_id] >= self.failure_threshold:
                    new_status = "OFFLINE"
                    logger.warning(
                        "Node %s is silent (missed %d probes). Marked OFFLINE.",
                        node_id,
                        self._consecutive_failures[node_id],
                    )
                else:
                    new_status = "DEGRADED"

            self._node_status[node_id] = new_status

        if old_status != new_status:
            for listener in self._listeners:
                try:
                    listener(node_id, old_status, new_status)
                except Exception as e:
                    logger.error("Error in health listener callback: %s", e)

    def probe_all(self) -> Dict[str, HealthCheckResult]:
        """Probe all registered nodes concurrently."""
        with self._lock:
            items = list(self._nodes.items())

        if not items:
            return {}

        results: Dict[str, HealthCheckResult] = {}
        with ThreadPoolExecutor(max_workers=min(len(items), 10)) as pool:
            futures = {
                pool.submit(self.probe_node, node_id, url): node_id
                for node_id, url in items
            }
            for fut in futures:
                res = fut.result()
                results[res.node_id] = res

        return results

    def get_status(self, node_id: str) -> str:
        """Get the current operational status of a node."""
        with self._lock:
            return self._node_status.get(node_id, "OFFLINE")

    def is_available(self, node_id: str) -> bool:
        """Return True if node is capable of receiving traffic."""
        with self._lock:
            return self._node_status.get(node_id, "OFFLINE") in ("HEALTHY", "DEGRADED")

    def get_all_statuses(self) -> Dict[str, str]:
        """Return a snapshot of all node statuses."""
        with self._lock:
            return dict(self._node_status)
