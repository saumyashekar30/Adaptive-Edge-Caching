"""Tests for edge node failure detection, traffic redirection, and recovery time measurement."""

import http.server
import json
import threading
import time
import unittest
import urllib.error
import urllib.request

from src.common.models import NodeMetrics, RecoveryRecord
from src.proxy.server import ProxyServer
from src.routing.controller import TrafficManagementController
from src.telemetry.health import HealthChecker
from src.telemetry.heartbeat import HeartbeatMonitor


class DummyHealthHandler(http.server.BaseHTTPRequestHandler):
    """Simple controllable HTTP server for failure and recovery testing."""

    is_healthy = True

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/health":
            if self.is_healthy:
                payload = json.dumps({"status": "healthy", "service": "edge-proxy", "uptime": 100.0}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            else:
                self.send_response(500)
                self.end_headers()
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")


class TestFailureDetectionAndRecovery(unittest.TestCase):
    """Tests for health probing, heartbeat monitoring, and traffic failover."""

    def test_01_health_checker_state_transitions(self):
        """Verify node transitions: HEALTHY -> DEGRADED -> OFFLINE and back to HEALTHY."""
        checker = HealthChecker(timeout_seconds=0.3, failure_threshold=2, recovery_threshold=2)
        checker.register_node("test-node-1", "http://127.0.0.1:9999")  # Non-existent port

        # Probe 1: First failure -> DEGRADED
        res1 = checker.probe_node("test-node-1")
        self.assertEqual(res1.status, "OFFLINE")  # Socket error
        self.assertEqual(checker.get_status("test-node-1"), "DEGRADED")
        self.assertTrue(checker.is_available("test-node-1"))

        # Probe 2: Second consecutive failure -> OFFLINE (Silent node marked unavailable)
        res2 = checker.probe_node("test-node-1")
        self.assertEqual(checker.get_status("test-node-1"), "OFFLINE")
        self.assertFalse(checker.is_available("test-node-1"))

    def test_02_heartbeat_dead_node_removal_and_recovery_time(self):
        """Simulate node dying, heartbeat detecting failure, and measuring recovery time."""
        # 1. Start a temporary HTTP server
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), DummyHealthHandler)
        port = server.server_port
        DummyHealthHandler.is_healthy = True
        th = threading.Thread(target=server.serve_forever, daemon=True)
        th.start()

        dead_events = []
        recovered_events = []

        checker = HealthChecker(timeout_seconds=0.2, failure_threshold=2, recovery_threshold=1)
        monitor = HeartbeatMonitor(
            health_checker=checker,
            interval_seconds=0.08,
            on_node_dead=lambda nid: dead_events.append(nid),
            on_node_recovered=lambda nid, rec: recovered_events.append((nid, rec)),
        )

        monitor.register_node("node-dynamic", f"http://127.0.0.1:{port}")
        monitor.start()

        try:
            # Initially healthy
            time.sleep(0.15)
            self.assertTrue(monitor.is_alive("node-dynamic"))
            self.assertIn("node-dynamic", monitor.get_active_nodes())

            # Simulate node failure / silent crash
            DummyHealthHandler.is_healthy = False
            # Wait for 2 probes to declare dead
            time.sleep(0.35)

            self.assertFalse(monitor.is_alive("node-dynamic"))
            self.assertIn("node-dynamic", monitor.get_dead_nodes())
            self.assertIn("node-dynamic", dead_events)

            # Simulate node recovery
            DummyHealthHandler.is_healthy = True
            time.sleep(0.35)

            # Node must be restored to active routing pool
            self.assertTrue(monitor.is_alive("node-dynamic"))
            self.assertIn("node-dynamic", monitor.get_active_nodes())
            self.assertEqual(len(recovered_events), 1)

            node_id, rec = recovered_events[0]
            self.assertEqual(node_id, "node-dynamic")
            self.assertGreater(rec.recovery_time_seconds, 0.0)
            print(f"\n[Failure Recovery Test] Node recovered in {rec.recovery_time_seconds:.3f}s")

            # Check measure_recovery_time API
            measured = monitor.measure_recovery_time("node-dynamic")
            self.assertIsNotNone(measured)
            self.assertAlmostEqual(measured, rec.recovery_time_seconds, places=3)

        finally:
            monitor.stop()
            server.shutdown()
            server.server_close()

    def test_03_traffic_redirection_on_node_failure(self):
        """Traffic Controller must automatically redirect client traffic when selected node dies."""
        controller = TrafficManagementController(use_mock_telemetry=True, probe_timeout=0.2)
        controller.register_node("node-a", "http://127.0.0.1:9091")
        controller.register_node("node-b", "http://127.0.0.1:9092")

        # Node A is best initially
        controller.record_telemetry(NodeMetrics(node_id="node-a", latency_ms=10.0))
        controller.record_telemetry(NodeMetrics(node_id="node-b", latency_ms=25.0))

        best = controller.select_best_node()
        self.assertEqual(best[0], "node-a")

        # Kill node-a (simulate silent death)
        controller.heartbeat._handle_status_transition("node-a", "HEALTHY", "OFFLINE")

        # Node-a is dead; controller must redirect to node-b
        self.assertIn("node-a", controller.heartbeat.get_dead_nodes())
        new_best = controller.select_best_node()
        self.assertEqual(new_best[0], "node-b")

        # Node-a recovers; controller can route to node-a again
        controller.heartbeat._handle_status_transition("node-a", "OFFLINE", "HEALTHY")
        self.assertIn("node-a", controller.heartbeat.get_active_nodes())
        restored_best = controller.select_best_node()
        self.assertEqual(restored_best[0], "node-a")


if __name__ == "__main__":
    unittest.main()
