"""Configuration loader and schema validation for Adaptive Edge Caching."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

from .models import ExperimentConfig, NodeConfig, RoutingWeights, WorkloadConfig

logger = logging.getLogger("config")


class ConfigError(ValueError):
    """Raised when a configuration file is missing required fields or has
    values outside their valid range. Carries the offending file path so
    callers can surface a useful error instead of a bare KeyError/ValueError."""


def load_yaml_or_json(file_path: str | Path) -> Dict[str, Any]:
    """Load configuration from either a YAML or JSON file."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {file_path}")

    content = path.read_text(encoding="utf-8")

    if path.suffix.lower() in (".yaml", ".yml"):
        if HAS_YAML:
            loaded = yaml.safe_load(content)
            return loaded if isinstance(loaded, dict) else {}
        else:
            # Simple fallback line-based parser for basic yaml configs
            data: Dict[str, Any] = {}
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" in line:
                    key, val = line.split(":", 1)
                    key = key.strip()
                    val = val.strip()
                    try:
                        if val.isdigit():
                            data[key] = int(val)
                        elif val.replace(".", "", 1).isdigit():
                            data[key] = float(val)
                        elif val.lower() in ("true", "false"):
                            data[key] = val.lower() == "true"
                        else:
                            data[key] = val.strip("\"'")
                    except Exception:
                        data[key] = val
            return data
    else:
        return json.loads(content)


def load_routing_weights(config_path: Optional[str | Path] = None) -> RoutingWeights:
    """Load routing weights from config file or return default RoutingWeights."""
    if config_path is None:
        # Check standard default locations
        candidates = [
            Path("config/routing.yaml"),
            Path("config/routing.json"),
            Path("../config/routing.yaml"),
        ]
        for c in candidates:
            if c.is_file():
                config_path = c
                break

    if config_path is not None and Path(config_path).is_file():
        try:
            raw = load_yaml_or_json(config_path)
            weights_data = raw.get("weights", raw)
            return RoutingWeights.from_dict(weights_data)
        except Exception as e:
            logger.warning("Failed to load weights from %s: %s. Using defaults.", config_path, e)

    return RoutingWeights()


def load_node_configs(nodes_dir: Optional[str | Path] = None) -> Dict[str, Dict[str, Any]]:
    """Load all node configurations from config/nodes directory."""
    if nodes_dir is None:
        candidates = [Path("config/nodes"), Path("../config/nodes")]
        for c in candidates:
            if c.is_dir():
                nodes_dir = c
                break

    nodes: Dict[str, Dict[str, Any]] = {}
    if nodes_dir is not None and Path(nodes_dir).is_dir():
        for p in Path(nodes_dir).glob("*.yaml"):
            try:
                cfg = load_yaml_or_json(p)
                node_id = cfg.get("id", p.stem)
                nodes[node_id] = cfg
            except Exception as e:
                logger.warning("Error reading node config %s: %s", p, e)
    return nodes


_VALID_POLICIES = {"lru", "lfu", "ttl", "adaptive"}


def load_node_config(file_path: str | Path) -> NodeConfig:
    """Load and validate a single `config/nodes/*.yaml` file into a typed NodeConfig.

    Raises ConfigError with a descriptive message for missing/invalid fields
    rather than letting a KeyError/TypeError surface from deep inside EdgeNode.
    """
    path = Path(file_path)
    raw = load_yaml_or_json(path)

    node_id = raw.get("id") or raw.get("node_id") or path.stem
    if not isinstance(node_id, str) or not node_id.strip():
        raise ConfigError(f"{path}: 'id' must be a non-empty string")

    port = raw.get("port", 8080)
    if not isinstance(port, int) or not (1 <= port <= 65535):
        raise ConfigError(f"{path}: 'port' must be an integer in [1, 65535], got {port!r}")

    capacity_mb = float(raw.get("capacity_mb", 50))
    if capacity_mb <= 0:
        raise ConfigError(f"{path}: 'capacity_mb' must be > 0, got {capacity_mb!r}")

    max_object_size_mb = float(raw.get("max_object_size_mb", 5))
    if max_object_size_mb <= 0:
        raise ConfigError(f"{path}: 'max_object_size_mb' must be > 0, got {max_object_size_mb!r}")
    if max_object_size_mb > capacity_mb:
        raise ConfigError(
            f"{path}: 'max_object_size_mb' ({max_object_size_mb}) cannot exceed "
            f"'capacity_mb' ({capacity_mb})"
        )

    policy = str(raw.get("policy", "lru")).lower()
    if policy not in _VALID_POLICIES:
        raise ConfigError(f"{path}: 'policy' must be one of {sorted(_VALID_POLICIES)}, got {policy!r}")

    origin_url = raw.get("origin_url", "http://127.0.0.1:8000")
    if not isinstance(origin_url, str) or not origin_url.startswith(("http://", "https://")):
        raise ConfigError(f"{path}: 'origin_url' must be an http(s) URL, got {origin_url!r}")

    peers_raw = raw.get("peers") or []
    if not isinstance(peers_raw, list):
        raise ConfigError(f"{path}: 'peers' must be a list of URLs, got {type(peers_raw).__name__}")
    peers: List[str] = []
    for p in peers_raw:
        if not isinstance(p, str) or not p.startswith(("http://", "https://")):
            raise ConfigError(f"{path}: each peer must be an http(s) URL, got {p!r}")
        peers.append(p.rstrip("/"))

    own_base = f"http://{raw.get('host', '127.0.0.1')}:{port}"
    if own_base in peers:
        raise ConfigError(f"{path}: node '{node_id}' lists itself ({own_base}) as a peer")

    return NodeConfig(
        node_id=node_id,
        host=str(raw.get("host", "0.0.0.0")),
        port=port,
        capacity_mb=capacity_mb,
        max_object_size_mb=max_object_size_mb,
        policy=policy,
        origin_url=origin_url,
        peers=peers,
        controller_url=raw.get("controller_url"),
        telemetry_interval_seconds=float(raw.get("telemetry_interval_seconds", 5.0)),
        default_ttl_seconds=(
            float(raw["default_ttl_seconds"]) if raw.get("default_ttl_seconds") is not None else None
        ),
        advertise_host=raw.get("advertise_host"),
    )


def load_experiment_config(file_path: str | Path) -> ExperimentConfig:
    """Load and validate a `config/experiments/*.yaml` file into a typed ExperimentConfig."""
    path = Path(file_path)
    raw = load_yaml_or_json(path)

    name = raw.get("experiment_name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(f"{path}: 'experiment_name' is required")

    duration = float(raw.get("duration_seconds", 60))
    if duration <= 0:
        raise ConfigError(f"{path}: 'duration_seconds' must be > 0")

    num_clients = int(raw.get("num_clients", 10))
    if num_clients <= 0:
        raise ConfigError(f"{path}: 'num_clients' must be > 0")

    wl_raw = raw.get("workload", {}) or {}
    distribution = str(wl_raw.get("distribution", "zipf")).lower()
    if distribution not in ("zipf", "uniform", "bursty"):
        raise ConfigError(f"{path}: workload.distribution must be zipf|uniform|bursty, got {distribution!r}")

    workload = WorkloadConfig(
        distribution=distribution,
        unique_objects=int(wl_raw.get("unique_objects", 100)),
        zipf_alpha=float(wl_raw.get("zipf_alpha", 1.1)),
        concurrency=int(wl_raw.get("concurrency", num_clients)),
        request_rate_per_sec=wl_raw.get("request_rate_per_sec"),
        burst_size=int(wl_raw.get("burst_size", 20)),
        burst_idle_seconds=float(wl_raw.get("burst_idle_seconds", 1.0)),
        seed=int(wl_raw.get("seed", 42)),
        warmup_requests=int(wl_raw.get("warmup_requests", 0)),
        request_timeout_seconds=float(wl_raw.get("request_timeout_seconds", 5.0)),
        object_size_mean_bytes=int(wl_raw.get("object_size_mean_bytes", 4096)),
        object_size_std_bytes=int(wl_raw.get("object_size_std_bytes", 0)),
        num_requests=int(wl_raw.get("num_requests", num_clients * 50)),
    )
    if workload.unique_objects <= 0:
        raise ConfigError(f"{path}: workload.unique_objects must be > 0")

    cache_raw = raw.get("cache", {}) or {}
    policies = cache_raw.get("policies") or ["lru"]
    if not isinstance(policies, list) or not policies:
        raise ConfigError(f"{path}: cache.policies must be a non-empty list")
    for p in policies:
        if str(p).lower() not in _VALID_POLICIES and str(p).lower() != "none":
            raise ConfigError(f"{path}: cache.policies entry {p!r} is not a recognized policy")

    origin_raw = raw.get("origin", {}) or {}

    return ExperimentConfig(
        experiment_name=name,
        duration_seconds=duration,
        num_clients=num_clients,
        workload=workload,
        cache_capacity_mb=float(cache_raw.get("capacity_mb", 2.0)),
        cache_max_object_size_kb=float(cache_raw.get("max_object_size_kb", 64.0)),
        policies=[str(p).lower() for p in policies],
        origin_base_latency_ms=float(origin_raw.get("base_latency_ms", 60.0)),
        origin_jitter_ms=float(origin_raw.get("jitter_ms", 10.0)),
        repetitions=int(raw.get("repetitions", 1)),
        mode=str(raw.get("mode", "self_managed")).lower(),
        target_url=raw.get("target_url"),
        raw=raw,
    )
