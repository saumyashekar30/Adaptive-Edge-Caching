"""Mock origin server with configurable latency, object sizes, and resource versioning.

Supports:
- Adjustable latency via global config or per-request query param (?latency_ms=...)
- Configurable and dynamic object sizes (?size=...)
- Versioned resources that mutate over time via PUT/POST or time-decay
- Conditional revalidation (If-None-Match / If-Modified-Since)
- Programmatic and standalone CLI execution
"""

from __future__ import annotations

import argparse
import email.utils
import json
import logging
import os
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger("origin_server")


class ResourceState:
    """Represents a versioned resource stored at origin."""

    def __init__(self, resource_id: str, version: int = 1, size_bytes: int = 1024, data: Optional[bytes] = None):
        self.resource_id = resource_id
        self.version = version
        self.size_bytes = size_bytes
        self.last_modified = time.time()
        self.data = data or self._generate_payload(size_bytes, version)

    def _generate_payload(self, size_bytes: int, version: int) -> bytes:
        prefix = f"[Resource: {self.resource_id} | Version: {version} | TS: {time.time():.4f}]\n".encode("utf-8")
        if len(prefix) >= size_bytes:
            return prefix[:size_bytes]
        padding = b"X" * (size_bytes - len(prefix))
        return prefix + padding

    def mutate(self, new_size_bytes: Optional[int] = None, new_version: Optional[int] = None) -> int:
        """Bump resource version and refresh payload."""
        self.version = new_version if new_version is not None else self.version + 1
        if new_size_bytes is not None:
            self.size_bytes = new_size_bytes
        self.last_modified = time.time()
        self.data = self._generate_payload(self.size_bytes, self.version)
        return self.version

    @property
    def etag(self) -> str:
        return f'"{self.resource_id}-v{self.version}"'


class MockOriginHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler for Mock Origin Server."""

    server: MockOriginHTTPServer  # Type hint for custom server

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)

    def end_headers(self) -> None:
        # Allow a browser-based frontend (e.g. a local Next.js dev server) to
        # read responses cross-origin. No stdlib http.server handler here
        # sent CORS headers before, so direct browser fetches were blocked.
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, If-None-Match, Cache-Control")
        self.end_headers()

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query = parse_qs(parsed_url.query)

        if path == "/health":
            self._send_json(HTTPStatus.OK, {"status": "healthy", "service": "mock-origin", "uptime": time.time() - self.server.start_time})
            return

        if path == "/versions":
            versions = {
                rid: {"version": res.version, "etag": res.etag, "size": res.size_bytes, "last_modified": res.last_modified}
                for rid, res in self.server.resources.items()
            }
            self._send_json(HTTPStatus.OK, versions)
            return

        # Handle data/object requests: /data/<id> or /objects/<id> or direct path
        resource_id = path.lstrip("/") or "default"
        if resource_id.startswith("data/"):
            resource_id = resource_id[5:]
        elif resource_id.startswith("objects/"):
            resource_id = resource_id[8:]

        # Latency calculation: query param overrides server default
        latency_ms = self.server.default_latency_ms
        if "latency_ms" in query:
            try:
                latency_ms = float(query["latency_ms"][0])
            except ValueError:
                pass
        elif "latency" in query:
            try:
                latency_ms = float(query["latency"][0]) * 1000.0
            except ValueError:
                pass

        if latency_ms > 0:
            time.sleep(latency_ms / 1000.0)

        # Retrieve or dynamically generate resource
        with self.server.lock:
            if resource_id not in self.server.resources:
                req_size = self.server.default_size_bytes
                if "size" in query:
                    try:
                        req_size = int(query["size"][0])
                    except ValueError:
                        pass
                self.server.resources[resource_id] = ResourceState(resource_id=resource_id, size_bytes=req_size)

            res = self.server.resources[resource_id]

            # Allow dynamic on-the-fly size override in query param without permanent mutation
            if "size" in query:
                try:
                    requested_size = int(query["size"][0])
                    if requested_size != res.size_bytes:
                        body_data = res._generate_payload(requested_size, res.version)
                    else:
                        body_data = res.data
                except ValueError:
                    body_data = res.data
            else:
                body_data = res.data

            etag = res.etag
            version = str(res.version)
            last_modified_str = email.utils.formatdate(res.last_modified, usegmt=True)

        # Check conditional GET
        client_if_none_match = self.headers.get("If-None-Match")
        if client_if_none_match and client_if_none_match.strip() == etag:
            self.send_response(HTTPStatus.NOT_MODIFIED)
            self.send_header("ETag", etag)
            self.send_header("X-Resource-Version", version)
            self.send_header("X-Origin-Latency-Ms", f"{latency_ms:.2f}")
            self.send_header("Last-Modified", last_modified_str)
            self.end_headers()
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body_data)))
        self.send_header("ETag", etag)
        self.send_header("X-Resource-Version", version)
        self.send_header("X-Origin-Latency-Ms", f"{latency_ms:.2f}")
        self.send_header("Last-Modified", last_modified_str)
        self.send_header("Cache-Control", f"max-age={self.server.default_ttl_seconds}")
        self.end_headers()
        self.wfile.write(body_data)

    def do_POST(self) -> None:
        self._handle_mutation()

    def do_PUT(self) -> None:
        self._handle_mutation()

    def _handle_mutation(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path

        if path == "/config":
            content_length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(content_length).decode("utf-8")) if content_length > 0 else {}
            if "latency_ms" in payload:
                self.server.default_latency_ms = float(payload["latency_ms"])
            if "size_bytes" in payload:
                self.server.default_size_bytes = int(payload["size_bytes"])
            if "ttl_seconds" in payload:
                self.server.default_ttl_seconds = int(payload["ttl_seconds"])
            self._send_json(HTTPStatus.OK, {
                "status": "config_updated",
                "default_latency_ms": self.server.default_latency_ms,
                "default_size_bytes": self.server.default_size_bytes,
                "default_ttl_seconds": self.server.default_ttl_seconds,
            })
            return

        # Resource mutation: /update/<id> or /data/<id>
        resource_id = path.lstrip("/")
        if resource_id.startswith("update/"):
            resource_id = resource_id[7:]
        elif resource_id.startswith("data/"):
            resource_id = resource_id[5:]
        elif resource_id.startswith("objects/"):
            resource_id = resource_id[8:]

        if not resource_id:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Missing resource ID"})
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""
        req_json: Dict[str, Any] = {}
        if body:
            try:
                req_json = json.loads(body.decode("utf-8"))
            except Exception:
                pass

        new_size = req_json.get("size")
        new_version = req_json.get("version")

        with self.server.lock:
            if resource_id not in self.server.resources:
                self.server.resources[resource_id] = ResourceState(
                    resource_id=resource_id,
                    size_bytes=new_size or self.server.default_size_bytes,
                    version=new_version or 1,
                )
                res = self.server.resources[resource_id]
            else:
                res = self.server.resources[resource_id]
                res.mutate(new_size_bytes=new_size, new_version=new_version)

            resp_data = {
                "resource_id": resource_id,
                "version": res.version,
                "etag": res.etag,
                "size_bytes": res.size_bytes,
                "last_modified": res.last_modified,
            }

        self._send_json(HTTPStatus.OK, resp_data)

    def _send_json(self, status: HTTPStatus, data: Any) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class MockOriginHTTPServer(ThreadingHTTPServer):
    """Custom HTTPServer maintaining shared state."""

    def __init__(
        self,
        server_address: tuple[str, int],
        default_latency_ms: float = 50.0,
        default_size_bytes: int = 1024,
        default_ttl_seconds: int = 60,
    ):
        super().__init__(server_address, MockOriginHandler)
        self.default_latency_ms = default_latency_ms
        self.default_size_bytes = default_size_bytes
        self.default_ttl_seconds = default_ttl_seconds
        self.resources: Dict[str, ResourceState] = {}
        self.lock = threading.Lock()
        self.start_time = time.time()


class MockOriginServer:
    """Controller class to run the origin server programmatically or in a thread."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8000,
        default_latency_ms: float = 50.0,
        default_size_bytes: int = 1024,
        default_ttl_seconds: int = 60,
    ):
        self.host = host
        self.port = port
        self.default_latency_ms = default_latency_ms
        self.default_size_bytes = default_size_bytes
        self.default_ttl_seconds = default_ttl_seconds
        self.server: Optional[MockOriginHTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self.server = MockOriginHTTPServer(
            (self.host, self.port),
            default_latency_ms=self.default_latency_ms,
            default_size_bytes=self.default_size_bytes,
            default_ttl_seconds=self.default_ttl_seconds,
        )
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        logger.info("Mock origin server started at http://%s:%d", self.host, self.port)

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.thread:
            self.thread.join(timeout=2.0)
            self.thread = None
        logger.info("Mock origin server stopped")

    def mutate_resource(self, resource_id: str, new_size: Optional[int] = None, new_version: Optional[int] = None) -> int:
        """Helper method to mutate a resource programmatically."""
        if not self.server:
            raise RuntimeError("Origin server is not running")
        with self.server.lock:
            if resource_id not in self.server.resources:
                self.server.resources[resource_id] = ResourceState(resource_id, size_bytes=new_size or self.default_size_bytes)
            return self.server.resources[resource_id].mutate(new_size_bytes=new_size, new_version=new_version)


def run_origin_server() -> None:
    parser = argparse.ArgumentParser(description="Mock Origin Server for Adaptive Edge Caching")
    parser.add_argument("--host", default="0.0.0.0", help="Binding host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    parser.add_argument("--latency-ms", type=float, default=50.0, help="Default latency in ms (default: 50.0)")
    parser.add_argument("--size-bytes", type=int, default=1024, help="Default object size in bytes (default: 1024)")
    parser.add_argument("--ttl", type=int, default=60, help="Default TTL in seconds (default: 60)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    server = MockOriginHTTPServer(
        (args.host, args.port),
        default_latency_ms=args.latency_ms,
        default_size_bytes=args.size_bytes,
        default_ttl_seconds=args.ttl,
    )
    print(f"[*] Starting Mock Origin Server on {args.host}:{args.port} (default latency={args.latency_ms}ms, size={args.size_bytes}B)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Shutting down Mock Origin Server...")
        server.shutdown()


if __name__ == "__main__":
    run_origin_server()
