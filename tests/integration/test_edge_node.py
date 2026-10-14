"""Integration tests for src/edge/node.py, src/edge/registry.py, and the
workload/experiment/analysis pipeline (Riddhi: Distributed Edge Runtime).

The existing tests/integration/test_multi_node.py and test_end_to_end.py
already exercise ProxyServer directly (local hit, peer hit, origin
fallback, failover/recovery, consistency, eviction). This file instead
targets the EdgeNode runtime itself -- the thing this package adds on top
of ProxyServer -- plus the configuration loader, peer registry validation,
telemetry snapshot/push, and the experiment pipeline end to end.
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from src.common.config import ConfigError, load_experiment_config, load_node_config
from src.common.models import NodeConfig
from src.edge.node import EdgeNode
from src.edge.registry import ConsistentHashRing, PeerRegistry, PeerRegistryError
from src.origin.server import MockOriginServer
from src.routing.controller import TrafficControllerServer, TrafficManagementController


def _http_get(url, timeout=5.0):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, dict(resp.headers), resp.read()


class TestPeerRegistry(unittest.TestCase):
    """TEST 10 (config/registry validation) coverage for the peer registry."""

    def test_rejects_self_reference_by_url(self):
        registry = PeerRegistry(self_node_id="node1", self_url="http://127.0.0.1:9000")
        with self.assertRaises(PeerRegistryError):
            registry.add_peer("http://127.0.0.1:9000")

    def test_rejects_malformed_url(self):
        registry = PeerRegistry(self_node_id="node1", self_url="http://127.0.0.1:9000")
        with self.assertRaises(PeerRegistryError):
            registry.add_peer("not-a-url")

    def test_deduplicates_peers(self):
        registry = PeerRegistry(self_node_id="node1", self_url="http://127.0.0.1:9000")
        registry.add_peer("http://127.0.0.1:9001")
        registry.add_peer("http://127.0.0.1:9001/")  # trailing slash, same peer
        self.assertEqual(len(registry), 1)

    def test_eligible_peers_excludes_marked_unavailable(self):
        registry = PeerRegistry(self_node_id="node1", self_url="http://127.0.0.1:9000")
        registry.add_peer("http://127.0.0.1:9001")
        registry.add_peer("http://127.0.0.1:9002")
        registry.mark_unavailable("http://127.0.0.1:9001")
        self.assertEqual(registry.eligible_peer_urls(), ["http://127.0.0.1:9002"])

    def test_consistent_hash_ring_deterministic(self):
        ring = ConsistentHashRing(["node1", "node2", "node3"])
        self.assertEqual(ring.get_node("resource-a"), ring.get_node("resource-a"))
        self.assertIn(ring.get_node("resource-a"), ("node1", "node2", "node3"))


class TestNodeConfigValidation(unittest.TestCase):
    """TEST 10 coverage: invalid node ids/ports/capacities/peers must fail loudly."""

    def _write(self, tmpdir, content):
        path = Path(tmpdir) / "node.yaml"
        path.write_text(content)
        return path

    def test_invalid_port_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "id: n1\nport: 999999\n")
            with self.assertRaises(ConfigError):
                load_node_config(path)

    def test_invalid_capacity_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "id: n1\nport: 8080\ncapacity_mb: -5\n")
            with self.assertRaises(ConfigError):
                load_node_config(path)

    def test_max_object_exceeding_capacity_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "id: n1\nport: 8080\ncapacity_mb: 1\nmax_object_size_mb: 5\n")
            with self.assertRaises(ConfigError):
                load_node_config(path)

    def test_invalid_policy_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "id: n1\nport: 8080\npolicy: not_a_policy\n")
            with self.assertRaises(ConfigError):
                load_node_config(path)

    def test_self_referencing_peer_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                tmp,
                "id: n1\nhost: 127.0.0.1\nport: 8080\npeers:\n  - \"http://127.0.0.1:8080\"\n",
            )
            with self.assertRaises(ConfigError):
                load_node_config(path)

    def test_valid_config_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                tmp,
                "id: n1\nhost: 127.0.0.1\nport: 8080\ncapacity_mb: 10\n"
                "max_object_size_mb: 2\npolicy: lfu\norigin_url: http://127.0.0.1:8000\n",
            )
            cfg = load_node_config(path)
            self.assertEqual(cfg.node_id, "n1")
            self.assertEqual(cfg.policy, "lfu")
            self.assertEqual(cfg.capacity_bytes, 10 * 1024 * 1024)

    def test_experiment_config_requires_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "exp.yaml"
            path.write_text("duration_seconds: 10\n")
            with self.assertRaises(ConfigError):
                load_experiment_config(path)


class TestEdgeNodeLifecycle(unittest.TestCase):
    """TEST 1/3/4 analogues driven through the EdgeNode class (not raw ProxyServer),
    plus TEST 4 (peer failure) and TEST 7 (telemetry)."""

    @classmethod
    def setUpClass(cls):
        cls.origin = MockOriginServer(host="127.0.0.1", port=0, default_latency_ms=20.0)
        cls.origin.start()
        origin_url = f"http://127.0.0.1:{cls.origin.port}"

        cls.controller = TrafficManagementController(use_mock_telemetry=False, heartbeat_interval=0.3, probe_timeout=0.4)
        cls.controller_server = TrafficControllerServer(host="127.0.0.1", port=0, controller=cls.controller)
        cls.controller_server.start()
        time.sleep(0.1)
        controller_url = f"http://127.0.0.1:{cls.controller_server.server.server_port}"

        cls.node_a = EdgeNode(NodeConfig(
            node_id="edge-a", host="127.0.0.1", port=0, capacity_mb=5, max_object_size_mb=1,
            policy="lru", origin_url=origin_url, peers=[],
            controller_url=controller_url, telemetry_interval_seconds=0.3,
        ))
        cls.node_a.start()

        cls.node_b = EdgeNode(NodeConfig(
            node_id="edge-b", host="127.0.0.1", port=0, capacity_mb=5, max_object_size_mb=1,
            policy="adaptive", origin_url=origin_url,
            peers=[f"http://127.0.0.1:{cls.node_a.config.port}"],
            controller_url=controller_url, telemetry_interval_seconds=0.3,
        ))
        cls.node_b.start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.node_b.stop()
        cls.node_a.stop()
        cls.controller_server.stop()
        cls.origin.stop()

    def test_01_local_hit_through_edge_node(self):
        url_a = f"http://127.0.0.1:{self.node_a.config.port}/data/shared-item"
        status1, headers1, _ = _http_get(url_a)
        self.assertEqual(status1, 200)
        self.assertEqual(headers1.get("X-Cache"), "MISS")

        status2, headers2, _ = _http_get(url_a)
        self.assertEqual(status2, 200)
        self.assertEqual(headers2.get("X-Cache"), "HIT")

    def test_02_peer_hit_through_edge_node(self):
        # node_a already has shared-item cached from test_01; node_b hasn't fetched it.
        url_b = f"http://127.0.0.1:{self.node_b.config.port}/data/shared-item"
        status, headers, _ = _http_get(url_b)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-Cache"), "PEER_HIT")
        self.assertEqual(headers.get("X-Peer-Node-Id"), "edge-a")

    def test_03_origin_fallback_when_no_peer_has_it(self):
        url_b = f"http://127.0.0.1:{self.node_b.config.port}/data/never-seen-before"
        status, headers, _ = _http_get(url_b)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-Cache"), "MISS")

    def test_04_peer_failure_falls_back_without_hanging(self):
        # Point node_b at a peer that doesn't exist; request must still
        # complete quickly by falling through to origin, never hang.
        self.node_b.peer_registry.add_peer("http://127.0.0.1:1")  # nothing listens here
        self.node_b.proxy.peer_client.add_peer("http://127.0.0.1:1")
        start = time.time()
        status, headers, _ = _http_get(
            f"http://127.0.0.1:{self.node_b.config.port}/data/peer-failure-probe", timeout=5.0
        )
        elapsed = time.time() - start
        self.assertEqual(status, 200)
        self.assertLess(elapsed, 4.0, "request should not hang waiting on a dead peer")

    def test_05_telemetry_counts_match_observed_outcomes(self):
        node = EdgeNode(NodeConfig(
            node_id="edge-telemetry-probe", host="127.0.0.1", port=0, capacity_mb=5,
            max_object_size_mb=1, policy="lru", origin_url=f"http://127.0.0.1:{self.origin.port}",
        ))
        node.start()
        try:
            base = f"http://127.0.0.1:{node.config.port}/data/telemetry-item"
            _http_get(base)  # MISS -> origin_fetch
            _http_get(base)  # HIT -> local_hit
            _http_get(base)  # HIT -> local_hit
            snap = node.get_telemetry_snapshot()
            self.assertEqual(snap.total_requests, 3)
            self.assertAlmostEqual(snap.hit_ratio, 2 / 3, places=3)
        finally:
            node.stop()

    def test_06_telemetry_reaches_controller(self):
        time.sleep(0.6)  # at least one push interval (0.3s) must have elapsed
        status, _, body = _http_get(f"http://127.0.0.1:{self.controller_server.server.server_port}/telemetry")
        data = json.loads(body)
        self.assertIn("edge-a", data)
        self.assertIn("edge-b", data)

    def test_07_health_endpoint_reports_node_identity(self):
        status, _, body = _http_get(f"http://127.0.0.1:{self.node_a.config.port}/health")
        data = json.loads(body)
        self.assertEqual(data["node_id"], "edge-a")
        self.assertEqual(data["status"], "healthy")


class TestConcurrentAccess(unittest.TestCase):
    """TEST 12: concurrent cache access through one EdgeNode must not corrupt
    cache state or deadlock the node runtime."""

    def test_concurrent_requests_do_not_corrupt_or_deadlock(self):
        from concurrent.futures import ThreadPoolExecutor

        origin = MockOriginServer(host="127.0.0.1", port=0, default_latency_ms=5.0)
        origin.start()
        node = EdgeNode(NodeConfig(
            node_id="edge-concurrency-probe", host="127.0.0.1", port=0, capacity_mb=1,
            max_object_size_mb=1, policy="adaptive", origin_url=f"http://127.0.0.1:{origin.port}",
        ))
        node.start()
        try:
            base = f"http://127.0.0.1:{node.config.port}"
            keys = [f"shared-{i}" for i in range(5)]

            def worker(i):
                key = keys[i % len(keys)]
                status, headers, _ = _http_get(f"{base}/data/{key}", timeout=5.0)
                return status, headers.get("X-Cache")

            start = time.time()
            with ThreadPoolExecutor(max_workers=20) as pool:
                results = list(pool.map(worker, range(100)))
            elapsed = time.time() - start

            self.assertLess(elapsed, 10.0, "concurrent access should not deadlock")
            self.assertTrue(all(status == 200 for status, _ in results))

            stats = node.proxy.cache_store.stats()
            self.assertGreaterEqual(stats["object_count"], 0)
            self.assertLessEqual(stats["current_size_bytes"], stats["max_capacity_bytes"])

            snap = node.get_telemetry_snapshot()
            self.assertEqual(snap.total_requests, 100)
        finally:
            node.stop()
            origin.stop()


class TestExperimentPipeline(unittest.TestCase):
    """TEST 11: run a small deterministic workload end-to-end through
    run_experiment.py (self_managed mode) and verify analyze.py produces
    real artifacts from it -- not placeholders."""

    def test_self_managed_experiment_and_analysis_produce_artifacts(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
        from scripts.run_experiment import run_experiment
        from scripts.analyze import analyze
        from src.common.models import ExperimentConfig, WorkloadConfig

        with tempfile.TemporaryDirectory() as tmp:
            cfg = ExperimentConfig(
                experiment_name="pipeline_smoke_test",
                workload=WorkloadConfig(
                    distribution="uniform", num_requests=24, unique_objects=6,
                    concurrency=4, warmup_requests=4, seed=1,
                ),
                cache_capacity_mb=1,
                cache_max_object_size_kb=64,
                policies=["lru", "lfu"],
                origin_base_latency_ms=2.0,
                repetitions=1,
                mode="self_managed",
            )
            out_dir = run_experiment(cfg, results_root=tmp)
            self.assertTrue((out_dir / "summary.json").is_file())
            raw_files = list(out_dir.glob("*.raw.jsonl"))
            self.assertEqual(len(raw_files), 2)  # one per policy

            manifest = json.loads((out_dir / "summary.json").read_text())
            self.assertEqual(len(manifest["summaries"]), 2)
            for summary in manifest["summaries"]:
                self.assertEqual(summary["total_requests"], 24)

            analysis_dir = analyze(str(out_dir))
            self.assertTrue((analysis_dir / "summary_table.csv").is_file())
            self.assertTrue((analysis_dir / "hit_ratio_by_policy.png").is_file())


if __name__ == "__main__":
    unittest.main()
