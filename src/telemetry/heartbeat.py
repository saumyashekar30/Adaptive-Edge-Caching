"""Periodic heartbeat monitor removing dead nodes from routing, redirecting traffic, and measuring recovery time."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, List, Optional, Set

try:
    from ..common.models import RecoveryRecord
    from .health import HealthChecker
except ImportError:
    from src.common.models import RecoveryRecord  # type: ignore
    from src.telemetry.health import HealthChecker  # type: ignore

logger = logging.getLogger("heartbeat_monitor")


class HeartbeatMonitor:
    """Runs periodic heartbeat probes, manages active/dead node sets, and measures MTTR / recovery times."""

    def __init__(
        self,
        health_checker: HealthChecker,
        interval_seconds: float = 1.0,
        on_node_dead: Optional[Callable[[str], None]] = None,
        on_node_recovered: Optional[Callable[[str, RecoveryRecord], None]] = None,
    ) -> None:
        self.health_checker = health_checker
        self.interval_seconds = interval_seconds
        self.on_node_dead = on_node_dead
        self.on_node_recovered = on_node_recovered

        self._lock = threading.RLock()
        self._active_nodes: Set[str] = set()
        self._dead_nodes: Set[str] = set()
        self._failure_timestamps: Dict[str, float] = {}
        self._recovery_history: List[RecoveryRecord] = []

        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Hook into health checker status changes
        self.health_checker.add_listener(self._handle_status_transition)

    def register_node(self, node_id: str, url: str) -> None:
        """Register node with health checker and add to active set."""
        with self._lock:
            self._active_nodes.add(node_id)
            self._dead_nodes.discard(node_id)
        self.health_checker.register_node(node_id, url)

    def deregister_node(self, node_id: str) -> None:
        """Deregister node."""
        with self._lock:
            self._active_nodes.discard(node_id)
            self._dead_nodes.discard(node_id)
            self._failure_timestamps.pop(node_id, None)
        self.health_checker.deregister_node(node_id)

    def start(self) -> None:
        """Start the heartbeat background thread."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._run_loop, daemon=True, name="HeartbeatThread")
            self._thread.start()
        logger.info("HeartbeatMonitor started (interval: %.2fs)", self.interval_seconds)

    def stop(self) -> None:
        """Stop the heartbeat background thread."""
        with self._lock:
            if not self._running:
                return
            self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            self._thread = None
        logger.info("HeartbeatMonitor stopped")

    def _run_loop(self) -> None:
        """Periodic probe execution loop."""
        while self._running:
            try:
                self.probe_now()
            except Exception as e:
                logger.error("Error during heartbeat probe cycle: %s", e)
            
            # Sleep in short increments to allow rapid clean shutdown
            start_sleep = time.time()
            while self._running and (time.time() - start_sleep < self.interval_seconds):
                time.sleep(0.05)

    def probe_now(self) -> None:
        """Explicitly run a heartbeat probe cycle immediately."""
        self.health_checker.probe_all()

    def _handle_status_transition(self, node_id: str, old_status: str, new_status: str) -> None:
        """Invoked by HealthChecker whenever a node status changes."""
        now = time.time()
        record_to_emit: Optional[RecoveryRecord] = None
        dead_callback_needed = False
        recovered_callback_needed = False

        with self._lock:
            # Case 1: Node Failed (Transitioned to OFFLINE)
            if new_status == "OFFLINE" and old_status != "OFFLINE":
                self._active_nodes.discard(node_id)
                self._dead_nodes.add(node_id)
                self._failure_timestamps[node_id] = now
                dead_callback_needed = True
                logger.critical(
                    "[HEARTBEAT ALERT] Node %s declared DEAD. Removed from active routing pool.",
                    node_id,
                )

            # Case 2: Node Recovered (Transitioned from OFFLINE to HEALTHY)
            elif new_status == "HEALTHY" and old_status == "OFFLINE":
                failed_time = self._failure_timestamps.pop(node_id, now)
                recovery_duration = now - failed_time
                record = RecoveryRecord(
                    node_id=node_id,
                    failed_at=failed_time,
                    recovered_at=now,
                    recovery_time_seconds=recovery_duration,
                )
                self._recovery_history.append(record)
                self._dead_nodes.discard(node_id)
                self._active_nodes.add(node_id)
                record_to_emit = record
                recovered_callback_needed = True
                logger.info(
                    "[HEARTBEAT RECOVERY] Node %s RECOVERED in %.3f seconds. Restored to routing pool.",
                    node_id,
                    recovery_duration,
                )

            # Case 3: Degraded -> Active still, but logged
            elif new_status == "DEGRADED":
                logger.warning("[HEARTBEAT WARN] Node %s is in DEGRADED condition.", node_id)

        # Dispatch callbacks outside lock
        if dead_callback_needed and self.on_node_dead:
            try:
                self.on_node_dead(node_id)
            except Exception as e:
                logger.error("Error executing on_node_dead for %s: %s", node_id, e)

        if recovered_callback_needed and self.on_node_recovered and record_to_emit:
            try:
                self.on_node_recovered(node_id, record_to_emit)
            except Exception as e:
                logger.error("Error executing on_node_recovered for %s: %s", node_id, e)

    def is_alive(self, node_id: str) -> bool:
        """Check if a node is currently active and alive."""
        with self._lock:
            return node_id in self._active_nodes

    def get_active_nodes(self) -> Set[str]:
        """Return a copy of the current set of healthy/active node IDs."""
        with self._lock:
            return set(self._active_nodes)

    def get_dead_nodes(self) -> Set[str]:
        """Return a copy of the current set of dead/offline node IDs."""
        with self._lock:
            return set(self._dead_nodes)

    def get_recovery_records(self) -> List[RecoveryRecord]:
        """Return historical recovery metrics."""
        with self._lock:
            return list(self._recovery_history)

    def get_latest_recovery(self, node_id: str) -> Optional[RecoveryRecord]:
        """Get the most recent recovery event for a specific node."""
        with self._lock:
            for rec in reversed(self._recovery_history):
                if rec.node_id == node_id:
                    return rec
            return None

    def measure_recovery_time(self, node_id: str) -> Optional[float]:
        """Return recovery duration in seconds for node's latest failure, if any."""
        latest = self.get_latest_recovery(node_id)
        return latest.recovery_time_seconds if latest else None
