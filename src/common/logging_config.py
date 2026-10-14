"""Structured logging configuration.

Emits one JSON object per line (JSON Lines) so application and experiment
events can be parsed by `scripts/analyze.py` or any log aggregator without
regex scraping. Every record carries: timestamp, component, node_id (when
known), event, level, message, and an optional free-form `fields` dict for
measurements. Response bodies and secrets must never be passed in `fields`.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "taskName",
}


class JsonLinesFormatter(logging.Formatter):
    """Renders each LogRecord as a single JSON line."""

    def __init__(self, component: str, node_id: Optional[str] = None) -> None:
        super().__init__()
        self.component = component
        self.node_id = node_id

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": round(record.created, 6),
            "level": record.levelname,
            "component": self.component,
            "event": getattr(record, "event", record.getMessage()),
            "message": record.getMessage(),
        }
        if self.node_id:
            payload["node_id"] = self.node_id

        # Pull through any extra fields the caller attached via `extra=`.
        extras = {k: v for k, v in record.__dict__.items() if k not in _RESERVED and not k.startswith("_")}
        extras.pop("event", None)
        if extras:
            payload["fields"] = extras

        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging(
    component: str,
    node_id: Optional[str] = None,
    level: str = "INFO",
    log_dir: Optional[str | Path] = None,
) -> logging.Logger:
    """Configure a component-scoped logger that writes JSON Lines to stdout,
    and additionally to `<log_dir>/<component>.jsonl` when `log_dir` is given.

    Safe to call multiple times for the same component (handlers are reset
    each call rather than accumulating duplicates).
    """
    logger = logging.getLogger(component)
    logger.setLevel(level.upper())
    logger.propagate = False
    for h in list(logger.handlers):
        logger.removeHandler(h)

    formatter = JsonLinesFormatter(component=component, node_id=node_id)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    if log_dir is not None:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path / f"{component}.jsonl")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def log_event(
    logger: logging.Logger,
    event: str,
    level: str = "INFO",
    request_id: Optional[str] = None,
    error: Optional[str] = None,
    **measurements: Any,
) -> None:
    """Emit one structured event with arbitrary measurement kwargs.

    Example: log_event(logger, "peer_fetch", node_id="node1",
                        request_id=req_id, latency_ms=12.3, peer="node2")
    """
    extra: Dict[str, Any] = {"event": event, **measurements}
    if request_id is not None:
        extra["request_id"] = request_id
    if error is not None:
        extra["error"] = error
    logger.log(getattr(logging, level.upper(), logging.INFO), event, extra=extra)


def get_logger(component: str) -> logging.Logger:
    """Fetch a previously configured logger, or a bare one if never configured."""
    return logging.getLogger(component)
