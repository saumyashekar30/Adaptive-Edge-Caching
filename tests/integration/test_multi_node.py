"""Integration test suite for multi-node edge caching cluster.

Validates:
1. Cold miss -> Origin fetch flow across 3 edge nodes.
2. Warm hit flow on local edge cache.
3. Peer-to-peer cache fetch: Node 2 queries Node 1 via /peer-cache/{id} on local miss (Shared Seam api_contracts.md Sec 5).
4. Traffic Controller telemetry-guided dynamic routing across the 3-node cluster.
5. Dynamic failover rerouting when a node is killed mid-operation.
6. Node recovery and re-addition to active routing pool.
"""

from __future__ import annotations

import json
import time
import unittest
import urllib.error
import urllib.request

from src.common.models import NodeMetrics
from src.edge.peer_client import PeerClient
from src.origin.server import MockOriginServer
from src.proxy.server import ProxyServer
from src.routing.controller import TrafficControllerServer, TrafficManagementController


class TestMultiNodeClusterIntegration(unittest.TestCase):
    """End-to-end integration test across 3 Edge Nodes, Origin, and Traffic Controller."""

    @classmethod
    def setUpClass(cls):
        cls.origin_port = 9300
        cls.node1_port = 9301
        cls.node2_port = 9302
        cls.node3_port = 9303
        cls.controller_port = 9304

        # 1. Start authoritative Origin Server (simulated latency 40ms)
        cls.origin = MockOriginServer(
            host="127.0.0.1",
            port=cls.origin_port,
            default_latency_ms=40.0,
            default_size_bytes=1024,
        )
        cls.origin.start()

        # Peer configurations
        node1_peers = [f"http://127.0.0.1:{cls.node2_port}", f"http://127.0.0.1:{cls.node3_port}"]
        node2_peers = [f"http://127.0.0.1:{cls.node1_port}", f"http://127.0.0.1:{cls.node3_port}"]
        node3_peers = [f"http://127.0.0.1:{cls.node1_port}", f"http://127.0.0.1:{cls.node2_port}"]

        # 2. Start 3 Edge Proxy Nodes with peer-to-peer peering
        cls.node1 = ProxyServer(
            host="127.0.0.1",
            port=cls.node1_port,
            origin_url=f"http://127.0.0.1:{cls.origin_port}",
            node_id="edge-node-1",
            peers=node1_peers,
            policy_name="adaptive",
        )
        cls.node1.start()

        cls.node2 = ProxyServer(
            host="127.0.0.1",
            port=cls.node2_port,
            origin_url=f"http://127.0.0.1:{cls.origin_port}",
            node_id="edge-node-2",
            peers=node2_peers,
            policy_name="adaptive",
        )
        cls.node2.start()

        cls.node3 = ProxyServer(
            host="127.0.0.1",
            port=cls.node3_port,
            origin_url=f"http://127.0.0.1:{cls.origin_port}",
            node_id="edge-node-3",
            peers=node3_peers,
            policy_name="adaptive",
        )
        cls.node3.start()

        # 3. Start Traffic Controller coordinating the 3 edge nodes
        cls.controller = TrafficManagementController(
            use_mock_telemetry=False,
            heartbeat_interval=0.2,
            probe_timeout=0.4,
        )
        cls.controller.register_node("edge-node-1", f"http://127.0.0.1:{cls.node1_port}")
        cls.controller.register_node("edge-node-2", f"http://127.0.0.1:{cls.node2_port}")
        cls.controller.register_node("edge-node-3", f"http://127.0.0.1:{cls.node3_port}")

        cls.controller_server = TrafficControllerServer(
            host="127.0.0.1",
            port=cls.controller_port,
            controller=cls.controller,
        )
        cls.controller_server.start()

        time.sleep(0.3)  # Allow sockets to bind

    @classmethod
    def tearDownClass(cls):
        cls.controller_server.stop()
        cls.node3.stop()
        cls.node2.stop()
        cls.node1.stop()
        cls.origin.stop()

    def _http_get(self, url: str):
        req = urllib.request.Request(url)
        start_t = time.perf_counter()
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            elapsed_ms = (time.perf_counter() - start_t) * 1000.0
            body = resp.read()
            headers = {k: v for k, v in resp.headers.items()}
            return resp.status, headers, body, elapsed_ms

    def test_01_cold_miss_on_node1(self):
        """Node 1 experiences a cold miss, retrieves from origin, and populates cache."""
        url = f"http://127.0.0.1:{self.node1_port}/data/media-file-99"
        status, headers, body, elapsed = self._http_get(url)

        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-Cache"), "MISS")
        self.assertEqual(headers.get("X-Edge-Node-Id"), "edge-node-1")
        self.assertGreaterEqual(elapsed, 30.0)  # Origin latency applied
        TestMultiNodeClusterIntegration.cold_miss_elapsed = elapsed

    def test_02_warm_hit_on_node1(self):
        """Subsequent query on Node 1 is a warm cache hit faster than cold miss."""
        url = f"http://127.0.0.1:{self.node1_port}/data/media-file-99"
        status, headers, body, elapsed = self._http_get(url)

        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-Cache"), "HIT")
        self.assertEqual(headers.get("X-Edge-Node-Id"), "edge-node-1")
        # Warm hit served from cache must be faster than cold miss
        if hasattr(TestMultiNodeClusterIntegration, "cold_miss_elapsed"):
            self.assertLess(elapsed, TestMultiNodeClusterIntegration.cold_miss_elapsed)

    def test_03_peer_to_peer_cache_fetch_node2_from_node1(self):
        """Node 2 queries peer Node 1 via /peer-cache/{id} on local miss, avoiding origin."""
        # Node 2 has not requested media-file-99 yet
        url = f"http://127.0.0.1:{self.node2_port}/data/media-file-99"
        status, headers, body, elapsed = self._http_get(url)

        self.assertEqual(status, 200)
        # Should be a PEER_HIT directly from Node 1
        self.assertIn(headers.get("X-Cache"), ("PEER_HIT", "HIT"))
        self.assertEqual(headers.get("X-Peer-Cache"), "HIT")
        self.assertEqual(headers.get("X-Peer-Node-Id"), "edge-node-1")
        self.assertEqual(headers.get("X-Edge-Node-Id"), "edge-node-2")
        print(f"\n[Peer Cache Flow] Node 2 retrieved from Node 1 peer cache in {elapsed:.2f}ms")

    def test_04_direct_peer_endpoint(self):
        """Verify direct /peer-cache/{resource_id} endpoint behavior per api_contracts.md."""
        # Hit on Node 1 for media-file-99
        hit_url = f"http://127.0.0.1:{self.node1_port}/peer-cache/media-file-99"
        status, headers, body, _ = self._http_get(hit_url)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-Peer-Cache"), "HIT")
        self.assertEqual(headers.get("X-Peer-Node-Id"), "edge-node-1")

        # Miss on Node 1 for unknown-key
        miss_url = f"http://127.0.0.1:{self.node1_port}/peer-cache/unknown-key-xyz"
        try:
            self._http_get(miss_url)
            self.fail("Expected 404 HTTPError")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)
            self.assertEqual(e.headers.get("X-Peer-Cache"), "MISS")

    def test_05_traffic_controller_routing_and_rerouting_on_failure(self):
        """Traffic Controller selects best node based on telemetry, then reroutes when node dies."""
        # Report telemetry favoring Node 1
        self.controller.record_telemetry(NodeMetrics(node_id="edge-node-1", latency_ms=8.0, status="HEALTHY"))
        self.controller.record_telemetry(NodeMetrics(node_id="edge-node-2", latency_ms=25.0, status="HEALTHY"))
        self.controller.record_telemetry(NodeMetrics(node_id="edge-node-3", latency_ms=50.0, status="HEALTHY"))

        controller_url = f"http://127.0.0.1:{self.controller_port}/data/media-file-99"

        # 1. First request through controller routes to optimal node (edge-node-1)
        status1, headers1, body1, _ = self._http_get(controller_url)
        self.assertEqual(status1, 200)
        self.assertEqual(headers1.get("X-Selected-Node"), "edge-node-1")

        # 2. Simulate killing edge-node-1
        print("\n[Failure Test] Stopping edge-node-1 to test dynamic failover...")
        self.node1.stop()

        # Controller heartbeat or mid-flight failover immediately catches dead node
        # Request through controller must succeed by rerouting to next best node (edge-node-2)
        status2, headers2, body2, _ = self._http_get(controller_url)
        self.assertEqual(status2, 200)
        self.assertIn(headers2.get("X-Selected-Node"), ("edge-node-2", "edge-node-3"))
        print(f"[Reroute Success] Rerouted client traffic to backup node: {headers2.get('X-Selected-Node')}")

        # 3. Bring edge-node-1 back up to test recovery
        print("[Recovery Test] Restarting edge-node-1...")
        self.node1 = ProxyServer(
            host="127.0.0.1",
            port=self.node1_port,
            origin_url=f"http://127.0.0.1:{self.origin_port}",
            node_id="edge-node-1",
            peers=[f"http://127.0.0.1:{self.node2_port}", f"http://127.0.0.1:{self.node3_port}"],
            policy_name="adaptive",
        )
        self.node1.start()
        time.sleep(0.5)  # Allow heartbeat monitor to probe and detect recovery

        # Explicitly update telemetry to restore Node 1 as optimal
        self.controller.record_telemetry(NodeMetrics(node_id="edge-node-1", latency_ms=6.0, status="HEALTHY"))
        self.controller.heartbeat.probe_now()

        # Controller should now be able to route to edge-node-1 again
        status3, headers3, body3, _ = self._http_get(controller_url)
        self.assertEqual(status3, 200)
        self.assertEqual(headers3.get("X-Selected-Node"), "edge-node-1")
        print(f"[Cluster Restored] Traffic Controller restored primary routing to {headers3.get('X-Selected-Node')}")


if __name__ == "__main__":
    unittest.main()
