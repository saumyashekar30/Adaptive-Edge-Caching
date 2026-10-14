"""Adaptive Cache Eviction Policy.

Scores objects on:
- Frequency (access count)
- Recency (elapsed time since last access)
- Object size (memory footprint penalty)
- Origin latency (RTT savings benefit)
- Bandwidth cost (transit network savings)
- Remaining capacity pressure (dynamic penalty scaling)

Weights are dynamically loaded from config/policies/adaptive.yaml with robust defaults.
"""

from __future__ import annotations

import logging
import math
import os
import time
from typing import Any, Dict, Mapping, Optional

try:
    from ..common.models import CacheMetadata
    from .base import BaseEvictionPolicy
except ImportError:
    from src.common.models import CacheMetadata  # type: ignore
    from src.cache.base import BaseEvictionPolicy  # type: ignore

logger = logging.getLogger("adaptive_policy")


def _load_yaml_config(config_path: str) -> Dict[str, Any]:
    """Load config from YAML file or fallback to parser."""
    if not os.path.exists(config_path):
        return {}

    try:
        import yaml  # type: ignore
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        # Minimal pure-Python YAML parser for simple key-value/nested dict structure
        result: Dict[str, Any] = {}
        current_section: Optional[str] = None
        with open(config_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" in line:
                    parts = line.split(":", 1)
                    k = parts[0].strip()
                    val_str = parts[1].split("#")[0].strip()
                    if not val_str:
                        current_section = k
                        result[current_section] = {}
                    else:
                        try:
                            if "." in val_str:
                                val: Any = float(val_str)
                            else:
                                val = int(val_str)
                        except ValueError:
                            if val_str.lower() in ("true", "yes"):
                                val = True
                            elif val_str.lower() in ("false", "no"):
                                val = False
                            else:
                                val = val_str.strip('"\'')
                        if current_section:
                            result[current_section][k] = val
                        else:
                            result[k] = val
        return result
    except Exception as e:
        logger.warning("Failed parsing %s: %s", config_path, e)
        return {}


class AdaptivePolicy(BaseEvictionPolicy):
    """Multi-metric adaptive eviction policy based on dynamic utility scoring."""

    def __init__(
        self,
        config_path: Optional[str] = None,
        weights: Optional[Dict[str, float]] = None,
        total_capacity_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self.config_path = config_path or self._find_default_config_path()
        self.total_capacity_bytes = total_capacity_bytes

        # Default weights
        self.w_freq = 0.25
        self.w_recency = 0.25
        self.w_latency = 0.20
        self.w_bandwidth = 0.10
        self.w_size = 0.10
        self.w_pressure = 0.10

        self._load_config(weights)

    def _find_default_config_path(self) -> str:
        # Search relative to file or cwd
        candidates = [
            os.path.join(os.path.dirname(__file__), "..", "..", "config", "policies", "adaptive.yaml"),
            os.path.join("config", "policies", "adaptive.yaml"),
            os.path.join("adaptive-edge-caching", "config", "policies", "adaptive.yaml"),
        ]
        for p in candidates:
            if os.path.exists(p):
                return os.path.abspath(p)
        return "config/policies/adaptive.yaml"

    def _load_config(self, explicit_weights: Optional[Dict[str, float]] = None) -> None:
        if explicit_weights:
            cfg_weights = explicit_weights
        else:
            data = _load_yaml_config(self.config_path)
            cfg_weights = data.get("weights", {})

        self.w_freq = float(cfg_weights.get("frequency", self.w_freq))
        self.w_recency = float(cfg_weights.get("recency", self.w_recency))
        self.w_latency = float(cfg_weights.get("origin_latency", self.w_latency))
        self.w_bandwidth = float(cfg_weights.get("bandwidth_cost", self.w_bandwidth))
        self.w_size = float(cfg_weights.get("size", self.w_size))
        self.w_pressure = float(cfg_weights.get("capacity_pressure", self.w_pressure))

        logger.debug(
            "AdaptivePolicy weights initialized: f=%.2f, r=%.2f, l=%.2f, b=%.2f, s=%.2f, p=%.2f",
            self.w_freq, self.w_recency, self.w_latency, self.w_bandwidth, self.w_size, self.w_pressure
        )

    @property
    def name(self) -> str:
        return "adaptive"

    def on_insert(self, key: str, metadata: CacheMetadata) -> None:
        pass

    def on_access(self, key: str, metadata: CacheMetadata) -> None:
        pass

    def on_evict(self, key: str) -> None:
        pass

    def calculate_utility(
        self,
        meta: CacheMetadata,
        now: float,
        norm_freq: float,
        norm_recency: float,
        norm_latency: float,
        norm_bandwidth: float,
        norm_size: float,
        pressure: float,
    ) -> float:
        """Calculate utility score U(o) for a single candidate.

        Higher score = higher value to keep in cache.
        Lower score = prime candidate for eviction.
        """
        # Dynamic size penalty increases as cache approaches capacity
        size_penalty = (self.w_size + (self.w_pressure * pressure)) * norm_size

        utility = (
            (self.w_freq * norm_freq)
            + (self.w_recency * norm_recency)
            + (self.w_latency * norm_latency)
            + (self.w_bandwidth * norm_bandwidth)
            - size_penalty
        )
        return utility

    def select_victim(
        self,
        candidates: Mapping[str, CacheMetadata],
        needed_bytes: int = 0
    ) -> Optional[str]:
        if not candidates:
            return None

        now = time.time()

        # Calculate current capacity pressure
        total_used = sum(m.size_bytes for m in candidates.values())
        capacity = max(self.total_capacity_bytes, total_used + needed_bytes)
        rem_bytes = max(0, capacity - total_used)
        pressure = 1.0 - (rem_bytes / capacity) if capacity > 0 else 1.0

        # Extract metric ranges for min-max normalization
        freqs = [m.access_count for m in candidates.values()]
        recencies = [1.0 / (1.0 + max(0.0, now - m.last_accessed_at)) for m in candidates.values()]
        latencies = [max(0.0, m.origin_latency_ms) for m in candidates.values()]
        bandwidths = [max(0.0, m.bandwidth_cost) for m in candidates.values()]
        sizes = [m.size_bytes for m in candidates.values()]

        min_f, max_f = min(freqs), max(freqs)
        min_r, max_r = min(recencies), max(recencies)
        min_l, max_l = min(latencies), max(latencies)
        min_b, max_b = min(bandwidths), max(bandwidths)
        min_s, max_s = min(sizes), max(sizes)

        def normalize(val: float, low: float, high: float) -> float:
            if math.isclose(low, high):
                return 0.5
            return (val - low) / (high - low)

        min_utility = float("inf")
        victim_key: Optional[str] = None

        for key, meta in candidates.items():
            r_val = 1.0 / (1.0 + max(0.0, now - meta.last_accessed_at))
            nf = normalize(meta.access_count, min_f, max_f)
            nr = normalize(r_val, min_r, max_r)
            nl = normalize(meta.origin_latency_ms, min_l, max_l)
            nb = normalize(meta.bandwidth_cost, min_b, max_b)
            ns = normalize(meta.size_bytes, min_s, max_s)

            score = self.calculate_utility(meta, now, nf, nr, nl, nb, ns, pressure)

            if score < min_utility:
                min_utility = score
                victim_key = key

        return victim_key

    def reset(self) -> None:
        pass
