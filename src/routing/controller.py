"""Traffic Management Controller coordinating telemetry, health monitoring, and edge node routing."""

from __future__ import annotations

import argparse
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    from ..common.config import load_node_configs, load_routing_weights
    from ..common.models import NodeMetrics, NodeScore, RecoveryRecord, RoutingWeights
    from ..telemetry.health import HealthChecker
    from ..telemetry.heartbeat import HeartbeatMonitor
    from ..telemetry.metrics import MockTelemetryGenerator, TelemetryCollector
    from .scoring import RoutingScorer
    from .selector import NodeSelector
except ImportError:
    from src.common.config import load_node_configs, load_routing_weights  # type: ignore
    from src.common.models import NodeMetrics, NodeScore, RecoveryRecord, RoutingWeights  # type: ignore
    from src.telemetry.health import HealthChecker  # type: ignore
    from src.telemetry.heartbeat import HeartbeatMonitor  # type: ignore
    from src.telemetry.metrics import MockTelemetryGenerator, TelemetryCollector  # type: ignore
    from src.routing.scoring import RoutingScorer  # type: ignore
    from src.routing.selector import NodeSelector  # type: ignore

logger = logging.getLogger("traffic_controller")


class TrafficControllerHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler for the Traffic Management Controller."""

    server: ControllerHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)

    def do_GET(self) -> None:
        path = self.path

        # 1. Controller Administration & Telemetry APIs
        if path == "/health":
            cluster_health = self.server.controller.get_cluster_status()
            self._send_json(HTTPStatus.OK, cluster_health)
            return

        if path == "/nodes":
            nodes_info = self.server.controller.get_nodes_info()
            self._send_json(HTTPStatus.OK, nodes_info)
            return

        if path == "/telemetry":
            all_telemetry = self.server.controller.get_all_telemetry()
            self._send_json(HTTPStatus.OK, {k: v.to_dict() for k, v in all_telemetry.items()})
            return

        if path == "/weights":
            weights = self.server.controller.scorer.weights.to_dict()
            self._send_json(HTTPStatus.OK, weights)
            return

        # 2. Edge Routing Proxy Request Flow
        req_headers = {k: v for k, v in self.headers.items()}
        status, resp_headers, body = self.server.controller.route_request(
            path=path,
            method="GET",
            headers=req_headers,
        )

        self.send_response(status)
        for k, v in resp_headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        path = self.path
        content_length = int(self.headers.get("Content-Length", 0))
        body_bytes = self.rfile.read(content_length) if content_length > 0 else b"{}"

        try:
            data = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
        except Exception:
            data = {}

        # 1. Telemetry Ingestion API (Shared Seam: produced by Riddhi, consumed by Saumya)
        if path == "/telemetry":
            metrics = self.server.controller.record_telemetry(data)
            self._send_json(HTTPStatus.OK, {"status": "recorded", "metrics": metrics.to_dict()})
            return

        # 2. Dynamic weights update
        if path == "/weights":
            self.server.controller.scorer.update_weights(
                alpha=data.get("alpha"),
                beta=data.get("beta"),
                gamma=data.get("gamma"),
                delta=data.get("delta"),
            )
            self._send_json(HTTPStatus.OK, {
                "status": "updated",
                "weights": self.server.controller.scorer.weights.to_dict(),
            })
            return

        # 3. Node Registration API
        if path == "/nodes/register":
            node_id = data.get("node_id")
            url = data.get("url")
            if node_id and url:
                self.server.controller.register_node(node_id, url)
                self._send_json(HTTPStatus.OK, {"status": "registered", "node_id": node_id, "url": url})
            else:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Missing node_id or url"})
            return

        # 4. Node Deregistration API
        if path == "/nodes/deregister":
            node_id = data.get("node_id")
            if node_id:
                self.server.controller.deregister_node(node_id)
                self._send_json(HTTPStatus.OK, {"status": "deregistered", "node_id": node_id})
            else:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Missing node_id"})
            return

        # 5. Forward generic POST to optimal edge node
        req_headers = {k: v for k, v in self.headers.items()}
        status, resp_headers, body = self.server.controller.route_request(
            path=path,
            method="POST",
            headers=req_headers,
            body=body_bytes,
        )
        self.send_response(status)
        for k, v in resp_headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: HTTPStatus, data: Any) -> None:
        payload = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class ControllerHTTPServer(ThreadingHTTPServer):
    """Custom HTTP server binding to TrafficManagementController."""

    def __init__(self, server_address: Tuple[str, int], controller: TrafficManagementController):
        super().__init__(server_address, TrafficControllerHandler)
        self.controller = controller


class TrafficManagementController:
    """Ties telemetry, scoring, health checks, and heartbeats to dynamic request routing."""

    def __init__(
        self,
        weights: Optional[RoutingWeights] = None,
        use_mock_telemetry: bool = True,
        heartbeat_interval: float = 1.0,
        probe_timeout: float = 0.8,
        origin_fallback_url: Optional[str] = None,
    ) -> None:
        self.scorer = RoutingScorer(weights=weights)
        self.selector = NodeSelector(scorer=self.scorer, fallback_enabled=True)
        self.health_checker = HealthChecker(timeout_seconds=probe_timeout)
        self.heartbeat = HeartbeatMonitor(
            health_checker=self.health_checker,
            interval_seconds=heartbeat_interval,
            on_node_dead=self._on_node_dead,
            on_node_recovered=self._on_node_recovered,
        )
        self.telemetry = TelemetryCollector()
        self.use_mock_telemetry = use_mock_telemetry
        self.origin_fallback_url = origin_fallback_url

        self._lock = threading.RLock()
        self._nodes: Dict[str, str] = {}  # node_id -> base_url
        self._total_requests_routed = 0
        self._reroute_count = 0

    def register_node(self, node_id: str, url: str) -> None:
        """Register an edge node with the controller."""
        clean_url = url.rstrip("/")
        with self._lock:
            self._nodes[node_id] = clean_url

        self.heartbeat.register_node(node_id, clean_url)

        # Seed initial mock telemetry if enabled and not already recorded
        if self.use_mock_telemetry and self.telemetry.get_metrics(node_id) is None:
            mock_metrics = MockTelemetryGenerator.generate_scenario_metrics(node_id, "normal")
            self.telemetry.record_telemetry(mock_metrics)

        logger.info("Registered edge node %s at %s", node_id, clean_url)

    def deregister_node(self, node_id: str) -> None:
        """Deregister an edge node."""
        with self._lock:
            self._nodes.pop(node_id, None)
        self.heartbeat.deregister_node(node_id)
        logger.info("Deregistered edge node %s", node_id)

    def record_telemetry(self, data: Union[NodeMetrics, Dict[str, Any]]) -> NodeMetrics:
        """Ingest external telemetry (from Riddhi's metrics engine or probes)."""
        metrics = self.telemetry.record_telemetry(data)
        return metrics

    def _on_node_dead(self, node_id: str) -> None:
        """Callback triggered when a node is marked dead by heartbeat."""
        logger.warning("[Controller] Rerouting traffic away from dead node %s", node_id)
        # Update metrics status to OFFLINE so scoring treats it as ineligible
        existing = self.telemetry.get_metrics(node_id)
        if existing:
            existing.status = "OFFLINE"
            self.telemetry.record_telemetry(existing)

    def _on_node_recovered(self, node_id: str, record: RecoveryRecord) -> None:
        """Callback triggered when a node recovers."""
        logger.info(
            "[Controller] Node %s recovered (MTTR: %.3fs). Restoring to active routing pool.",
            node_id,
            record.recovery_time_seconds,
        )
        existing = self.telemetry.get_metrics(node_id)
        if existing:
            existing.status = "HEALTHY"
            self.telemetry.record_telemetry(existing)

    def select_best_node(self, exclude_nodes: Optional[Set[str]] = None) -> Optional[Tuple[str, float]]:
        """Select best edge node using scorer and active node set."""
        with self._lock:
            known_ids = set(self._nodes.keys())

        if not known_ids:
            return None

        active_ids = self.heartbeat.get_active_nodes()
        if exclude_nodes:
            active_ids = active_ids - exclude_nodes

        metrics_map = self.telemetry.get_all_metrics()

        # Fill metrics for any node that doesn't have one yet
        for node_id in known_ids:
            if node_id not in metrics_map:
                metrics_map[node_id] = MockTelemetryGenerator.generate_scenario_metrics(node_id, "normal")

        # Compute scores
        scores = self.scorer.score_nodes(metrics_map)
        best_id = self.selector.select_best_node(scores, active_node_ids=active_ids)

        if best_id:
            return best_id, scores[best_id].score
        return None

    def route_request(
        self,
        path: str,
        method: str = "GET",
        headers: Optional[Dict[str, str]] = None,
        body: Optional[bytes] = None,
        max_retries: int = 2,
    ) -> Tuple[int, Dict[str, str], bytes]:
        """Route client request to the optimal edge node with automatic failover."""
        headers = headers or {}
        excluded_nodes: Set[str] = set()

        with self._lock:
            self._total_requests_routed += 1

        for attempt in range(max_retries + 1):
            selection = self.select_best_node(exclude_nodes=excluded_nodes)
            if not selection:
                logger.error("No healthy edge nodes available to route %s %s", method, path)
                break

            node_id, score = selection
            node_url = self._nodes.get(node_id)
            if not node_url:
                excluded_nodes.add(node_id)
                continue

            target_url = f"{node_url.rstrip('/')}{path}"
            logger.debug("Routing request %s %s -> %s (score: %.3f)", method, path, target_url, score)

            start_t = time.perf_counter()
            try:
                # Forward request to edge node
                req = urllib.request.Request(
                    url=target_url,
                    data=body if method.upper() in ("POST", "PUT") else None,
                    headers={k: v for k, v in headers.items() if k.lower() not in ("host", "content-length")},
                    method=method,
                )

                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    elapsed_ms = (time.perf_counter() - start_t) * 1000.0
                    resp_body = resp.read()
                    resp_headers = {k: v for k, v in resp.headers.items()}
                    resp_headers["X-Selected-Node"] = node_id
                    resp_headers["X-Node-Score"] = f"{score:.2f}"
                    resp_headers["X-Controller-Time-Ms"] = f"{elapsed_ms:.2f}"
                    resp_headers["X-Controller"] = "adaptive-traffic-controller"
                    return resp.status, resp_headers, resp_body

            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                logger.warning(
                    "Selected node %s failed mid-flight for %s: %s. Attempting failover reroute...",
                    node_id,
                    path,
                    e,
                )
                with self._lock:
                    self._reroute_count += 1
                excluded_nodes.add(node_id)
                # Mark failed in health checker
                self.health_checker.probe_node(node_id)

        # Fallback to origin if configured
        if self.origin_fallback_url:
            origin_url = f"{self.origin_fallback_url.rstrip('/')}{path}"
            logger.info("All edge nodes failed. Falling back to direct origin retrieval: %s", origin_url)
            try:
                req = urllib.request.Request(url=origin_url, data=body, headers=headers, method=method)
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    resp_body = resp.read()
                    resp_headers = {k: v for k, v in resp.headers.items()}
                    resp_headers["X-Fallback"] = "origin"
                    return resp.status, resp_headers, resp_body
            except Exception as e:
                logger.error("Direct origin fallback also failed: %s", e)

        err_msg = json.dumps({"error": "503 Service Unavailable: No healthy edge nodes"}).encode("utf-8")
        return (
            503,
            {"Content-Type": "application/json", "Content-Length": str(len(err_msg))},
            err_msg,
        )

    def get_cluster_status(self) -> Dict[str, Any]:
        """Return high-level summary of the edge cluster."""
        with self._lock:
            total = len(self._nodes)
            active = self.heartbeat.get_active_nodes()
            dead = self.heartbeat.get_dead_nodes()
            return {
                "status": "healthy" if active else "degraded",
                "service": "traffic-controller",
                "total_nodes": total,
                "active_nodes": sorted(list(active)),
                "dead_nodes": sorted(list(dead)),
                "total_requests_routed": self._total_requests_routed,
                "reroute_count": self._reroute_count,
                "uptime": time.time(),
            }

    def get_nodes_info(self) -> List[Dict[str, Any]]:
        """Return detailed information on all registered nodes."""
        with self._lock:
            nodes_copy = dict(self._nodes)

        metrics_map = self.telemetry.get_all_metrics()
        scores = self.scorer.score_nodes(metrics_map)
        active_set = self.heartbeat.get_active_nodes()

        info: List[Dict[str, Any]] = []
        for node_id, url in nodes_copy.items():
            node_metric = metrics_map.get(node_id)
            node_score = scores.get(node_id)
            info.append({
                "node_id": node_id,
                "url": url,
                "is_active": node_id in active_set,
                "status": self.health_checker.get_status(node_id),
                "score": node_score.score if node_score else None,
                "metrics": node_metric.to_dict() if node_metric else None,
            })
        return info

    def get_all_telemetry(self) -> Dict[str, NodeMetrics]:
        """Return all node telemetry."""
        return self.telemetry.get_all_metrics()


class TrafficControllerServer:
    """Threaded controller runner for standalone or in-process execution."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8000,
        controller: Optional[TrafficManagementController] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.controller = controller or TrafficManagementController()
        self.server: Optional[ControllerHTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the HTTP server and heartbeat monitor."""
        self.controller.heartbeat.start()
        self.server = ControllerHTTPServer((self.host, self.port), self.controller)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name="ControllerServerThread")
        self.thread.start()
        logger.info("Traffic Controller server started on http://%s:%d", self.host, self.port)

    def stop(self) -> None:
        """Stop HTTP server and heartbeat monitor."""
        self.controller.heartbeat.stop()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
            self.thread = None
        logger.info("Traffic Controller stopped")


def run_traffic_controller() -> None:
    """CLI Entry point matching Dockerfile.controller."""
    parser = argparse.ArgumentParser(description="Adaptive Edge Caching Traffic Management Controller")
    parser.add_argument("--host", default="0.0.0.0", help="Binding host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    parser.add_argument("--config", default="config/routing.yaml", help="Path to routing config YAML")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    weights = load_routing_weights(args.config)
    controller = TrafficManagementController(weights=weights)

    # Auto-load configured nodes
    node_configs = load_node_configs()
    for node_id, cfg in node_configs.items():
        host = cfg.get("host", "127.0.0.1")
        port = cfg.get("port", 8080)
        url = f"http://{host}:{port}"
        controller.register_node(node_id, url)

    server = TrafficControllerServer(host=args.host, port=args.port, controller=controller)
    print(f"[*] Starting Traffic Controller on {args.host}:{args.port} with {len(node_configs)} nodes...")
    server.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[*] Stopping Traffic Controller...")
        server.stop()


if __name__ == "__main__":
    run_traffic_controller()
