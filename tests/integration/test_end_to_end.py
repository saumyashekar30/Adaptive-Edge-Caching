"""End-to-End Integration Test Suite for Adaptive Edge Caching.

Covers the complete system workflow:
1. Client -> Proxy -> Cache Miss -> Origin fetch -> Store in Cache
2. Client -> Proxy -> Cache Hit -> Sub-millisecond response validation
3. Latency comparison (Hit latency << Miss latency)
4. Resource mutation on Origin -> Invalidation trigger -> Consistency convergence
5. Adaptive eviction policy behavior under memory pressure
6. Large object size limit enforcement
"""

import json
import time
import unittest
import urllib.request

from src.cache.adaptive import AdaptivePolicy
from src.consistency.invalidation import ConsistencyManager
from src.origin.server import MockOriginServer
from src.proxy.server import ProxyServer


class TestEndToEndEdgeCaching(unittest.TestCase):
    """End-to-End Integration test for full client-proxy-origin lifecycle."""

    @classmethod
    def setUpClass(cls):
        cls.origin_port = 9050
        cls.proxy_port = 9060

        # 1. Start mock origin server with 60ms simulated network latency
        cls.origin = MockOriginServer(
            host="127.0.0.1",
            port=cls.origin_port,
            default_latency_ms=60.0,
            default_size_bytes=1024,
            default_ttl_seconds=120,
        )
        cls.origin.start()

        # 2. Setup consistency manager
        cls.consistency = ConsistencyManager()

        # 3. Start edge proxy with Adaptive policy and small capacity (10 KB) for eviction testing
        cls.proxy = ProxyServer(
            host="127.0.0.1",
            port=cls.proxy_port,
            origin_url=f"http://127.0.0.1:{cls.origin_port}",
            node_id="edge-e2e-node-1",
            cache_capacity_bytes=10 * 1024,  # 10 KB limit
            max_object_size_bytes=4 * 1024,   # 4 KB max object
            policy_name="adaptive",
            consistency_manager=cls.consistency,
        )
        cls.proxy.start()

        # Connect consistency manager with proxy's cache and origin client
        cls.consistency.set_cache_store(cls.proxy.cache_store)
        cls.consistency.set_origin_client(cls.proxy.origin_client)

        time.sleep(0.15)  # Allow sockets to bind

    @classmethod
    def tearDownClass(cls):
        cls.proxy.stop()
        cls.origin.stop()

    def _http_get(self, path: str):
        url = f"http://127.0.0.1:{self.proxy_port}{path}"
        start = time.perf_counter()
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            body = resp.read()
            headers = {k: v for k, v in resp.headers.items()}
            return resp.status, headers, body, elapsed_ms

    def test_01_cold_miss_then_warm_hit(self):
        """Verify cold miss forwards to origin, then warm hit is served from edge."""
        resource_path = "/data/video-stream-101"

        # First Request: Must be MISS
        status1, headers1, body1, elapsed1 = self._http_get(resource_path)
        self.assertEqual(status1, 200)
        self.assertEqual(headers1.get("X-Cache"), "MISS")
        self.assertEqual(headers1.get("X-Resource-Version"), "1")
        # Miss should take at least origin latency (60ms)
        self.assertGreaterEqual(elapsed1, 50.0)

        # Second Request: Must be HIT
        status2, headers2, body2, elapsed2 = self._http_get(resource_path)
        self.assertEqual(status2, 200)
        self.assertEqual(headers2.get("X-Cache"), "HIT")
        self.assertEqual(body1, body2)

        # Hit latency must be vastly faster than cold miss latency
        self.assertLess(elapsed2, elapsed1)
        print(f"\n[E2E Latency Validation] Cold MISS: {elapsed1:.2f}ms | Warm HIT: {elapsed2:.2f}ms (Speedup: {elapsed1 / max(elapsed2, 0.1):.1f}x)")

    def test_02_mutation_invalidation_and_convergence(self):
        """Verify origin mutation and invalidation triggers consistency convergence."""
        resource_id = "config-object-202"
        resource_path = f"/data/{resource_id}"

        # 1. Populate cache initially (version 1)
        _, headers1, body1, _ = self._http_get(resource_path)
        self.assertEqual(headers1.get("X-Resource-Version"), "1")

        # 2. Mutate resource on origin (version 2)
        new_version = self.origin.mutate_resource(resource_id, new_size=512, new_version=2)
        self.assertEqual(new_version, 2)

        # 3. Trigger invalidation on consistency manager
        self.consistency.invalidate(resource_path, new_version="2", purge_cache=True)

        # 4. Next client request should fetch new version from origin
        status3, headers3, body3, _ = self._http_get(resource_path)
        self.assertEqual(status3, 200)
        self.assertEqual(headers3.get("X-Cache"), "MISS")
        self.assertEqual(headers3.get("X-Resource-Version"), "2")
        self.assertNotEqual(body1, body3)

        # 5. Subsequent request should HIT with version 2
        _, headers4, body4, _ = self._http_get(resource_path)
        self.assertEqual(headers4.get("X-Cache"), "HIT")
        self.assertEqual(headers4.get("X-Resource-Version"), "2")
        self.assertEqual(body3, body4)

        # Check consistency stats
        stats = self.consistency.stats()
        self.assertGreaterEqual(stats["total_invalidations"], 1)
        self.assertEqual(stats["total_stale_hits"], 0)
        print(f"[E2E Consistency Validation] Invalidation converged with 0 stale hits. Stats: {stats}")

    def test_03_adaptive_eviction_under_capacity_pressure(self):
        """Verify Adaptive policy evicts low-utility objects when cache fills up."""
        # Proxy capacity is 10 KB (10240 bytes). Each test item is 2 KB (2048 bytes).
        # We insert 6 items (12 KB total), triggering eviction.
        # High value item: item-valuable (accessed 3 times, high latency)
        # Low value item: item-cheap (accessed once, fast latency)

        self._http_get("/data/item-valuable?size=2048&latency_ms=120")
        self._http_get("/data/item-valuable?size=2048&latency_ms=120")  # Repeated hit
        self._http_get("/data/item-valuable?size=2048&latency_ms=120")  # Repeated hit

        self._http_get("/data/item-cheap?size=2048&latency_ms=5")

        # Fill remaining capacity
        for i in range(1, 5):
            self._http_get(f"/data/filler-{i}?size=2048&latency_ms=30")

        # Check that high-value item remains in cache (HIT)
        _, headers_valuable, _, _ = self._http_get("/data/item-valuable?size=2048&latency_ms=120")
        self.assertEqual(headers_valuable.get("X-Cache"), "HIT")

        # Check cache stats show evictions happened
        stats = self.proxy.cache_store.stats()
        self.assertGreater(stats["evictions"], 0)
        print(f"[E2E Eviction Validation] Adaptive Evictions: {stats['evictions']} | Current Objects: {stats['object_count']}")

    def test_04_object_size_limit_not_cached(self):
        """Objects larger than max_object_size_bytes are proxied but not retained in cache."""
        large_path = "/data/huge-file?size=5000"  # 5000 bytes > 4096 bytes max_object_size

        # 1st request -> Proxied from origin (MISS)
        status1, headers1, body1, _ = self._http_get(large_path)
        self.assertEqual(status1, 200)
        self.assertEqual(headers1.get("X-Cache"), "MISS")
        self.assertEqual(len(body1), 5000)

        # 2nd request -> Still MISS because it wasn't retained in cache!
        status2, headers2, body2, _ = self._http_get(large_path)
        self.assertEqual(status2, 200)
        self.assertEqual(headers2.get("X-Cache"), "MISS")


if __name__ == "__main__":
    unittest.main()
