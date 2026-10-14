"""HTTP Proxy Server accepting client requests and adding X-Cache: HIT / MISS headers."""

from __future__ import annotations

import argparse
import json
import logging
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

try:
    from ..cache.adaptive import AdaptivePolicy
    from ..cache.lru import LRUPolicy
    from ..cache.store import CacheStore
    from .origin_client import OriginClient
    from .request_handler import ProxyRequestHandler
except ImportError:
    from src.cache.adaptive import AdaptivePolicy  # type: ignore
    from src.cache.lru import LRUPolicy  # type: ignore
    from src.cache.store import CacheStore  # type: ignore
    from src.proxy.origin_client import OriginClient  # type: ignore
    from src.proxy.request_handler import ProxyRequestHandler  # type: ignore

try:
    from ..edge.peer_client import PeerClient
except ImportError:
    try:
        from src.edge.peer_client import PeerClient  # type: ignore
    except ImportError:
        PeerClient = None  # type: ignore

logger = logging.getLogger("proxy_server")


class ProxyHTTPRequestHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler dispatching to ProxyRequestHandler."""

    server: ProxyHTTPServer  # Custom typed server reference

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)

    def do_GET(self) -> None:
        # 1. Edge admin/metrics endpoints
        if self.path == "/health":
            self._send_json(HTTPStatus.OK, {
                "status": "healthy",
                "service": "edge-proxy",
                "node_id": self.server.node_id,
                "uptime": time.time() - self.server.start_time,
            })
            return

        if self.path == "/cache/stats":
            self._send_json(HTTPStatus.OK, self.server.cache_store.stats())
            return

        # Peer-to-peer cache fetch endpoint (Shared Seam: api_contracts.md Section 5)
        if self.path.startswith("/peer-cache/"):
            resource_id = self.path[len("/peer-cache/"):].lstrip("/")
            candidates = [f"/data/{resource_id}", f"/{resource_id}", resource_id]
            entry = None
            for cand in candidates:
                lookup_res = self.server.cache_store.get(cand)
                if lookup_res.status == "HIT" and lookup_res.entry is not None:
                    entry = lookup_res.entry
                    break

            if entry is not None:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", entry.content_type)
                self.send_header("Content-Length", str(entry.size_bytes))
                self.send_header("X-Peer-Cache", "HIT")
                self.send_header("X-Peer-Node-Id", self.server.node_id)
                self.send_header("X-Resource-Version", entry.version)
                if entry.etag:
                    self.send_header("ETag", entry.etag)
                self.end_headers()
                self.wfile.write(entry.value)
            else:
                self.send_response(HTTPStatus.NOT_FOUND)
                self.send_header("Content-Type", "application/json")
                self.send_header("X-Peer-Cache", "MISS")
                self.send_header("X-Peer-Node-Id", self.server.node_id)
                err_payload = json.dumps({"error": "Resource not found in peer cache", "resource_id": resource_id}).encode("utf-8")
                self.send_header("Content-Length", str(len(err_payload)))
                self.end_headers()
                self.wfile.write(err_payload)
            return

        # 2. Regular proxy request flow
        req_headers = {k: v for k, v in self.headers.items()}
        status_code, resp_headers, body = self.server.handler.handle_request(
            raw_path=self.path,
            method="GET",
            headers=req_headers,
        )

        self.send_response(status_code)
        for h_key, h_val in resp_headers.items():
            self.send_header(h_key, h_val)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path == "/cache/invalidate":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
            try:
                data = json.loads(body)
            except Exception:
                data = {}

            key = data.get("key")
            prefix = data.get("prefix")

            if key:
                deleted = self.server.cache_store.invalidate(key)
                self._send_json(HTTPStatus.OK, {"status": "invalidated", "key": key, "success": deleted})
            elif prefix:
                count = self.server.cache_store.invalidate_by_prefix(prefix)
                self._send_json(HTTPStatus.OK, {"status": "invalidated", "prefix": prefix, "count": count})
            else:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Provide 'key' or 'prefix' to invalidate"})
            return

        # Non-GET requests pass through to origin
        req_headers = {k: v for k, v in self.headers.items()}
        status_code, resp_headers, body = self.server.handler.handle_request(
            raw_path=self.path,
            method="POST",
            headers=req_headers,
        )
        self.send_response(status_code)
        for h_key, h_val in resp_headers.items():
            self.send_header(h_key, h_val)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: HTTPStatus, data: Any) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class ProxyHTTPServer(ThreadingHTTPServer):
    """Custom HTTPServer maintaining proxy handler and cache reference."""

    def __init__(
        self,
        server_address: tuple[str, int],
        cache_store: CacheStore,
        origin_client: OriginClient,
        node_id: str = "edge-node-1",
        consistency_manager: Optional[Any] = None,
        peer_client: Optional[Any] = None,
    ):
        super().__init__(server_address, ProxyHTTPRequestHandler)
        self.cache_store = cache_store
        self.origin_client = origin_client
        self.node_id = node_id
        self.consistency_manager = consistency_manager
        self.peer_client = peer_client
        self.handler = ProxyRequestHandler(
            cache_store=cache_store,
            origin_client=origin_client,
            node_id=node_id,
            consistency_manager=consistency_manager,
            peer_client=peer_client,
        )
        self.start_time = time.time()


class ProxyServer:
    """Threaded controller for ProxyServer instance."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        origin_url: str = "http://127.0.0.1:8000",
        node_id: str = "edge-node-1",
        cache_capacity_bytes: int = 50 * 1024 * 1024,
        max_object_size_bytes: int = 5 * 1024 * 1024,
        policy_name: str = "lru",
        consistency_manager: Optional[Any] = None,
        peer_client: Optional[Any] = None,
        peers: Optional[list[str]] = None,
    ):
        self.host = host
        self.port = port
        self.origin_url = origin_url
        self.node_id = node_id
        self.consistency_manager = consistency_manager

        if peer_client is not None:
            self.peer_client = peer_client
        elif peers and PeerClient is not None:
            self.peer_client = PeerClient(requester_node_id=node_id, peer_urls=peers)
        else:
            self.peer_client = None

        # Initialize policy
        if policy_name.lower() == "adaptive":
            policy = AdaptivePolicy(total_capacity_bytes=cache_capacity_bytes)
        else:
            policy = LRUPolicy()

        self.cache_store = CacheStore(
            max_capacity_bytes=cache_capacity_bytes,
            max_object_size_bytes=max_object_size_bytes,
            policy=policy,
        )
        self.origin_client = OriginClient(origin_base_url=origin_url)
        self.server: Optional[ProxyHTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self.server = ProxyHTTPServer(
            (self.host, self.port),
            cache_store=self.cache_store,
            origin_client=self.origin_client,
            node_id=self.node_id,
            consistency_manager=self.consistency_manager,
            peer_client=self.peer_client,
        )
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        logger.info("Proxy server started at http://%s:%d (forwarding to %s)", self.host, self.port, self.origin_url)

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.thread:
            self.thread.join(timeout=2.0)
            self.thread = None
        logger.info("Proxy server stopped")


def run_proxy_server() -> None:
    parser = argparse.ArgumentParser(description="Adaptive Edge Caching Proxy Server")
    parser.add_argument("--host", default="0.0.0.0", help="Binding host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Port to listen on (default: 8080)")
    parser.add_argument("--origin", default="http://127.0.0.1:8000", help="Origin server URL (default: http://127.0.0.1:8000)")
    parser.add_argument("--node-id", default="edge-node-1", help="Edge node ID identifier")
    parser.add_argument("--policy", default="adaptive", choices=["lru", "lfu", "ttl", "adaptive"], help="Eviction policy")
    parser.add_argument("--capacity-mb", type=int, default=50, help="Max cache capacity in MB")
    parser.add_argument("--max-obj-mb", type=int, default=5, help="Max single object size in MB")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    proxy = ProxyServer(
        host=args.host,
        port=args.port,
        origin_url=args.origin,
        node_id=args.node_id,
        cache_capacity_bytes=args.capacity_mb * 1024 * 1024,
        max_object_size_bytes=args.max_obj_mb * 1024 * 1024,
        policy_name=args.policy,
    )
    print(f"[*] Starting Edge Proxy on {args.host}:{args.port} -> Origin: {args.origin} [Policy: {args.policy}]")
    try:
        proxy.start()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[*] Stopping Edge Proxy...")
        proxy.stop()


if __name__ == "__main__":
    run_proxy_server()
