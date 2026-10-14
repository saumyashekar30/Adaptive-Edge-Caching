"""Configuration loader and schema validation for Adaptive Edge Caching."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

from .models import RoutingWeights

logger = logging.getLogger("config")


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
