"""Unit and integration tests for Proxy server, request handler, and origin client."""

import json
import time
import unittest
import urllib.request
from typing import Dict

from src.cache.lru import LRUPolicy
from src.cache.store import CacheStore
from src.common.models import OriginResponse
from src.origin.server import MockOriginServer
from src.proxy.origin_client import OriginClient
from src.proxy.request_handler import ProxyRequestHandler
from src.proxy.server import ProxyServer


class FakeOriginClient:
    """Mock origin client for isolating ProxyRequestHandler unit tests."""

    def __init__(self):
        self.call_count = 0
        self.payload = b"Origin Response Data"
        self.version = "1.0"
        self.etag = '"fake-etag-1"'
        self.latency_ms = 45.0

    def fetch(self, path: str, headers: Dict[str, str] = None, query_params: Dict[str, str] = None) -> OriginResponse:
        self.call_count += 1
        return OriginResponse(
            status_code=200,
            body=self.payload,
            headers={
                "Content-Type": "application/octet-stream",
                "Cache-Control": "max-age=60",
                "ETag": self.etag,
                "X-Resource-Version": self.version,
            },
            latency_ms=self.latency_ms,
            version=self.version,
            etag=self.etag,
            content_type="application/octet-stream",
        )


class TestProxyRequestHandler(unittest.TestCase):
    """Tests for core proxy cache lookup and hit/miss decision logic."""

    def setUp(self):
        self.cache = CacheStore(max_capacity_bytes=10000, policy=LRUPolicy())
        self.fake_origin = FakeOriginClient()
        self.handler = ProxyRequestHandler(
            cache_store=self.cache,
            origin_client=self.fake_origin,  # type: ignore
            node_id="test-node-1",
        )

    def test_first_request_is_miss(self):
        status, headers, body = self.handler.handle_request("/data/item1")
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Cache"], "MISS")
        self.assertEqual(headers["X-Edge-Node-Id"], "test-node-1")
        self.assertEqual(headers["X-Resource-Version"], "1.0")
        self.assertEqual(body, b"Origin Response Data")
        self.assertEqual(self.fake_origin.call_count, 1)

    def test_second_request_is_hit(self):
        # 1st: Miss
        self.handler.handle_request("/data/item1")
        self.assertEqual(self.fake_origin.call_count, 1)

        # 2nd: Hit
        status, headers, body = self.handler.handle_request("/data/item1")
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Cache"], "HIT")
        self.assertEqual(body, b"Origin Response Data")
        # Origin should NOT have been called again!
        self.assertEqual(self.fake_origin.call_count, 1)

    def test_cache_control_bypass(self):
        # First put into cache
        self.handler.handle_request("/data/item1")
        self.assertEqual(self.fake_origin.call_count, 1)

        # Request with no-cache header
        status, headers, body = self.handler.handle_request(
            "/data/item1",
            headers={"Cache-Control": "no-cache"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Cache"], "BYPASS")
        self.assertEqual(self.fake_origin.call_count, 2)

    def test_url_normalization(self):
        # Different parameter order should resolve to the same normalized cache key
        self.handler.handle_request("/data/search?b=2&a=1")
        self.assertEqual(self.fake_origin.call_count, 1)

        # Swapped parameter order -> should HIT cache!
        status, headers, _ = self.handler.handle_request("/data/search?a=1&b=2")
        self.assertEqual(headers["X-Cache"], "HIT")
        self.assertEqual(self.fake_origin.call_count, 1)


class TestProxyServerLive(unittest.TestCase):
    """Functional test running real MockOriginServer and ProxyServer over localhost."""

    @classmethod
    def setUpClass(cls):
        # Start mock origin on port 9010
        cls.origin = MockOriginServer(host="127.0.0.1", port=9010, default_latency_ms=20.0)
        cls.origin.start()

        # Start proxy server on port 9020 pointing to origin
        cls.proxy = ProxyServer(
            host="127.0.0.1",
            port=9020,
            origin_url="http://127.0.0.1:9010",
            node_id="edge-test-1",
        )
        cls.proxy.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.proxy.stop()
        cls.origin.stop()

    def test_health_check(self):
        req = urllib.request.Request("http://127.0.0.1:9020/health")
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["status"], "healthy")
            self.assertEqual(data["node_id"], "edge-test-1")

    def test_proxy_end_to_end_headers(self):
        url = "http://127.0.0.1:9020/data/resource-alpha"

        # 1st request -> MISS
        req1 = urllib.request.Request(url)
        with urllib.request.urlopen(req1, timeout=5.0) as resp1:
            self.assertEqual(resp1.status, 200)
            self.assertEqual(resp1.headers.get("X-Cache"), "MISS")
            body1 = resp1.read()
            self.assertTrue(b"resource-alpha" in body1)

        # 2nd request -> HIT
        req2 = urllib.request.Request(url)
        with urllib.request.urlopen(req2, timeout=5.0) as resp2:
            self.assertEqual(resp2.status, 200)
            self.assertEqual(resp2.headers.get("X-Cache"), "HIT")
            body2 = resp2.read()
            self.assertEqual(body1, body2)

        # Check stats endpoint
        stats_req = urllib.request.Request("http://127.0.0.1:9020/cache/stats")
        with urllib.request.urlopen(stats_req, timeout=5.0) as stats_resp:
            self.assertEqual(stats_resp.status, 200)
            stats = json.loads(stats_resp.read().decode("utf-8"))
            self.assertGreaterEqual(stats["hits"], 1)
            self.assertGreaterEqual(stats["misses"], 1)


if __name__ == "__main__":
    unittest.main()
