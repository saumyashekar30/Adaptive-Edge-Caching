"""Edge node main service runtime.

EdgeNode composes the already-implemented proxy (Immanuel), cache/eviction
engine (Immanuel), consistency manager (Immanuel), peer client (this
package), and telemetry collector (this package) into one independently
runnable, independently configurable edge-cache instance -- and optionally
reports its telemetry to Saumya's routing controller.

It does not reimplement caching, proxying, or peer HTTP fetch logic; it only
wires existing components together and adds the node-identity, lifecycle,
and telemetry-push behavior the brief assigns to this module.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import threading
import time
from typing import Optional

try:
    from ..common.config import ConfigError, load_node_config
    from ..common.logging_config import configure_logging, log_event
    from ..common.models import NodeConfig, NodeMetrics
    from ..consistency.invalidation import ConsistencyManager
    from ..proxy.server import ProxyServer
    from ..telemetry.metrics import EdgeNodeTelemetry
    from .registry import PeerRegistry, PeerRegistryError
except ImportError:  # pragma: no cover - fallback for `python -m src.edge.node`
    from src.common.config import ConfigError, load_node_config  # type: ignore
    from src.common.logging_config import configure_logging, log_event  # type: ignore
    from src.common.models import NodeConfig, NodeMetrics  # type: ignore
    from src.consistency.invalidation import ConsistencyManager  # type: ignore
    from src.proxy.server import ProxyServer  # type: ignore
    from src.telemetry.metrics import EdgeNodeTelemetry  # type: ignore
    from src.edge.registry import PeerRegistry, PeerRegistryError  # type: ignore

logger = logging.getLogger("edge_node")


class EdgeNode:
    """One independently runnable edge-cache instance.

    Lifecycle: construct -> start() -> ... serving ... -> stop().
    Thread-safety: all mutable state lives in composed components that are
    already thread-safe (CacheStore uses an RLock, ProxyServer runs a
    ThreadingHTTPServer); EdgeNode itself only owns a daemon telemetry
    thread guarded by a threading.Event.
    """

    def __init__(self, config: NodeConfig) -> None:
        self.config = config
        self.node_id = config.node_id

        self.peer_registry = PeerRegistry(
            self_node_id=config.node_id,
            self_url=config.base_url,
        )
        for peer_url in config.peers:
            try:
                self.peer_registry.add_peer(peer_url)
            except PeerRegistryError as e:
                logger.warning("Skipping invalid peer for node %s: %s", config.node_id, e)

        self.consistency = ConsistencyManager()
        self.telemetry = EdgeNodeTelemetry(node_id=config.node_id)

        self.proxy = ProxyServer(
            host=config.host,
            port=config.port,
            origin_url=config.origin_url,
            node_id=config.node_id,
            cache_capacity_bytes=config.capacity_bytes,
            max_object_size_bytes=config.max_object_size_bytes,
            policy_name=config.policy,
            consistency_manager=self.consistency,
            peers=self.peer_registry.peer_urls() or None,
            default_ttl_seconds=config.default_ttl_seconds,
            stats_hook=self.telemetry.record,
        )
        # Let the telemetry collector relay live cache stats; the cache_store
        # already exists at construction time (ProxyServer builds it eagerly).
        self.telemetry.cache_store = self.proxy.cache_store

        self._telemetry_stop = threading.Event()
        self._telemetry_thread: Optional[threading.Thread] = None
        self._started = False

    # -- lifecycle -----------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        self.proxy.start()
        # The port may have been auto-assigned (port=0); keep config in sync
        # so later registration/telemetry calls advertise the real address.
        self.config.port = self.proxy.port

        self.consistency.set_cache_store(self.proxy.cache_store)
        self.consistency.set_origin_client(self.proxy.origin_client)

        self._register_with_controller()

        self._telemetry_stop.clear()
        self._telemetry_thread = threading.Thread(target=self._telemetry_loop, daemon=True)
        self._telemetry_thread.start()

        self._started = True
        log_event(logger, "node_started", node_id=self.node_id,
                   host=self.config.host, port=self.config.port,
                   policy=self.config.policy, peers=len(self.peer_registry))

    def stop(self) -> None:
        if not self._started:
            return
        self._telemetry_stop.set()
        if self._telemetry_thread is not None:
            self._telemetry_thread.join(timeout=3.0)
            self._telemetry_thread = None

        self._deregister_from_controller()
        self.proxy.stop()
        self._started = False
        log_event(logger, "node_stopped", node_id=self.node_id)

    # -- telemetry -------------------------------------------------------

    def _telemetry_loop(self) -> None:
        interval = max(0.5, self.config.telemetry_interval_seconds)
        while not self._telemetry_stop.is_set():
            try:
                snapshot = self.telemetry.snapshot()
                log_event(
                    logger, "telemetry_snapshot", node_id=self.node_id,
                    total_requests=snapshot.total_requests,
                    hit_ratio=snapshot.hit_ratio,
                    latency_ms=snapshot.latency_ms,
                    utilization_pct=snapshot.utilization_pct,
                )
                if self.config.controller_url:
                    ok = self.telemetry.push_to_controller(self.config.controller_url)
                    if not ok:
                        logger.debug("Telemetry push to controller failed for node %s", self.node_id)
            except Exception as e:  # telemetry must never crash the node
                logger.warning("Telemetry loop error on node %s: %s", self.node_id, e)
            self._telemetry_stop.wait(interval)

    def get_telemetry_snapshot(self) -> NodeMetrics:
        return self.telemetry.snapshot()

    # -- controller registration (best-effort, non-blocking to start()) --

    def _register_with_controller(self) -> None:
        if not self.config.controller_url:
            return
        try:
            import json
            import urllib.request
            payload = json.dumps({"node_id": self.node_id, "url": self.config.base_url}).encode("utf-8")
            req = urllib.request.Request(
                self.config.controller_url.rstrip("/") + "/nodes/register",
                data=payload, method="POST", headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=2.0):
                pass
            log_event(logger, "controller_registered", node_id=self.node_id,
                      controller_url=self.config.controller_url)
        except Exception as e:
            logger.warning("Could not register node %s with controller %s: %s",
                            self.node_id, self.config.controller_url, e)

    def _deregister_from_controller(self) -> None:
        if not self.config.controller_url:
            return
        try:
            import json
            import urllib.request
            payload = json.dumps({"node_id": self.node_id}).encode("utf-8")
            req = urllib.request.Request(
                self.config.controller_url.rstrip("/") + "/nodes/deregister",
                data=payload, method="POST", headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=2.0):
                pass
        except Exception as e:
            logger.debug("Could not deregister node %s from controller: %s", self.node_id, e)

    # -- convenience -----------------------------------------------------

    def health(self) -> dict:
        return {
            "node_id": self.node_id,
            "status": "running" if self._started else "stopped",
            "host": self.config.host,
            "port": self.config.port,
            "policy": self.config.policy,
            "peers": self.peer_registry.peer_urls(),
            "controller_url": self.config.controller_url,
        }

    @classmethod
    def from_config_file(cls, path: str, **overrides) -> "EdgeNode":
        config = load_node_config(path)
        for k, v in overrides.items():
            if v is not None and hasattr(config, k):
                setattr(config, k, v)
        return cls(config)


def _config_from_env(defaults: Optional[NodeConfig] = None) -> NodeConfig:
    """Build/override a NodeConfig purely from environment variables, for
    Docker entrypoints that pass config via `environment:` rather than a
    mounted YAML file (docker-compose.yml)."""
    base = defaults or NodeConfig(node_id=os.environ.get("NODE_ID", "edge-node-1"))

    def _env_float(name: str, current: float) -> float:
        val = os.environ.get(name)
        return float(val) if val is not None else current

    def _env_int(name: str, current: int) -> int:
        val = os.environ.get(name)
        return int(val) if val is not None else current

    peers_env = os.environ.get("PEERS")
    peers = [p.strip() for p in peers_env.split(",") if p.strip()] if peers_env else base.peers

    return NodeConfig(
        node_id=os.environ.get("NODE_ID", base.node_id),
        host=os.environ.get("NODE_HOST", base.host),
        port=_env_int("NODE_PORT", base.port),
        capacity_mb=_env_float("CAPACITY_MB", base.capacity_mb),
        max_object_size_mb=_env_float("MAX_OBJECT_SIZE_MB", base.max_object_size_mb),
        policy=os.environ.get("POLICY", base.policy).lower(),
        origin_url=os.environ.get("ORIGIN_URL", base.origin_url),
        peers=peers,
        controller_url=os.environ.get("CONTROLLER_URL", base.controller_url),
        telemetry_interval_seconds=_env_float("TELEMETRY_INTERVAL_SECONDS", base.telemetry_interval_seconds),
        default_ttl_seconds=base.default_ttl_seconds,
        advertise_host=os.environ.get("ADVERTISE_HOST", base.advertise_host),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Adaptive Edge Caching - Edge Node Runtime")
    parser.add_argument("--config", default=os.environ.get("NODE_CONFIG"),
                         help="Path to a config/nodes/*.yaml file (env var overrides still apply on top)")
    parser.add_argument("--log-dir", default=os.environ.get("LOG_DIR"), help="Optional directory for JSON Lines logs")
    args = parser.parse_args()

    if args.config:
        try:
            config = load_node_config(args.config)
        except ConfigError as e:
            print(f"[edge_node] Invalid node configuration: {e}")
            raise SystemExit(1)
        config = _config_from_env(defaults=config)
    else:
        config = _config_from_env()

    configure_logging(component="edge_node", node_id=config.node_id, log_dir=args.log_dir)

    node = EdgeNode(config)

    def _handle_signal(signum, frame):
        logger.info("Received signal %s, shutting down node %s", signum, config.node_id)
        node.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    print(f"[*] Starting EdgeNode '{config.node_id}' on {config.host}:{config.port} "
          f"-> origin {config.origin_url} [policy={config.policy}, peers={len(config.peers)}]")
    node.start()
    try:
        while True:
            time.sleep(1.0)
    except (KeyboardInterrupt, SystemExit):
        node.stop()


if __name__ == "__main__":
    main()
