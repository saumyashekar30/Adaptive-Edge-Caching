"""Registry tracking peer edge node states.

Deterministic, config-driven peer discovery for one EdgeNode. This module
only resolves *which* peer base URLs a node should talk to and validates
that list (self-reference/duplicate detection, URL sanity); it intentionally
does not duplicate PeerClient's per-request HTTP fetch logic or
HealthChecker's liveness probing (src/telemetry/health.py) -- those already
exist and are reused as-is.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger("edge_registry")


class PeerRegistryError(ValueError):
    """Raised for an unambiguous peer-configuration mistake (self-reference,
    duplicate node id, or a malformed peer URL) that should fail fast at
    startup rather than cause silent request loops later."""


@dataclass
class PeerInfo:
    node_id: str
    url: str


def _normalize(url: str) -> str:
    return url.rstrip("/").lower()


class PeerRegistry:
    """Deterministic, in-memory registry of a node's configured siblings.

    `self_node_id`/`self_url` identify the owning node so self-references can
    be rejected. Peers may be added either as plain base URLs (the schema
    actually used by config/nodes/*.yaml today) or as (node_id, url) pairs
    when node identity is also known (e.g. when cross-referencing sibling
    node config files).
    """

    def __init__(self, self_node_id: str, self_url: str, timeout_seconds: float = 1.0) -> None:
        self.self_node_id = self_node_id
        self.self_url = self_url
        self.timeout_seconds = timeout_seconds
        self._peers: Dict[str, PeerInfo] = {}  # keyed by normalized URL
        self._unavailable: set[str] = set()

    def add_peer(self, url: str, node_id: Optional[str] = None) -> None:
        if not isinstance(url, str) or not url:
            raise PeerRegistryError(f"Peer URL must be a non-empty string, got {url!r}")
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise PeerRegistryError(f"Peer URL '{url}' is not a valid http(s) URL")

        norm = _normalize(url)
        if norm == _normalize(self.self_url) or (node_id and node_id == self.self_node_id):
            raise PeerRegistryError(
                f"Node '{self.self_node_id}' cannot register itself ({url}) as a peer"
            )
        if norm in self._peers:
            logger.debug("Peer %s already registered for node %s, ignoring duplicate", url, self.self_node_id)
            return
        if node_id:
            existing_ids = {p.node_id for p in self._peers.values()}
            if node_id in existing_ids:
                raise PeerRegistryError(f"Duplicate peer node_id '{node_id}' for node '{self.self_node_id}'")

        self._peers[norm] = PeerInfo(node_id=node_id or url, url=url.rstrip("/"))

    def remove_peer(self, url: str) -> None:
        self._peers.pop(_normalize(url), None)
        self._unavailable.discard(_normalize(url))

    def mark_unavailable(self, url: str) -> None:
        """Bookkeeping hook: callers (e.g. EdgeNode's health loop) can mark a
        peer as currently unreachable so `eligible_peer_urls()` can exclude
        it, without ever blocking the request path itself."""
        self._unavailable.add(_normalize(url))

    def mark_available(self, url: str) -> None:
        self._unavailable.discard(_normalize(url))

    def peer_urls(self) -> List[str]:
        """All configured peer base URLs, deterministic order (insertion order)."""
        return [p.url for p in self._peers.values()]

    def eligible_peer_urls(self) -> List[str]:
        """Peer URLs not currently flagged unavailable. PeerClient itself
        already tolerates per-request failures via try/except + timeout, so
        this is an optional optimization, not a correctness requirement."""
        return [p.url for p in self._peers.values() if _normalize(p.url) not in self._unavailable]

    def __len__(self) -> int:
        return len(self._peers)

    @classmethod
    def from_urls(cls, self_node_id: str, self_url: str, peer_urls: Iterable[str], timeout_seconds: float = 1.0) -> "PeerRegistry":
        registry = cls(self_node_id=self_node_id, self_url=self_url, timeout_seconds=timeout_seconds)
        for url in peer_urls:
            registry.add_peer(url)
        return registry


class ConsistentHashRing:
    """Optional consistent-hashing peer-selection strategy.

    Not required for correctness (PeerClient already tries all configured
    peers linearly per request) and not wired in by default anywhere -- this
    exists so a future placement/selection strategy can deterministically
    map a resource key to a preferred peer/replica set without pulling in an
    external dependency. Pure stdlib (hashlib), O(log n) lookup via bisect.
    """

    def __init__(self, nodes: Optional[Iterable[str]] = None, replicas: int = 8) -> None:
        self.replicas = replicas
        self._ring: Dict[int, str] = {}
        self._sorted_keys: List[int] = []
        for n in nodes or []:
            self.add_node(n)

    @staticmethod
    def _hash(key: str) -> int:
        return int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16)

    def add_node(self, node: str) -> None:
        for i in range(self.replicas):
            h = self._hash(f"{node}#{i}")
            self._ring[h] = node
        self._sorted_keys = sorted(self._ring.keys())

    def remove_node(self, node: str) -> None:
        for i in range(self.replicas):
            h = self._hash(f"{node}#{i}")
            self._ring.pop(h, None)
        self._sorted_keys = sorted(self._ring.keys())

    def get_node(self, key: str) -> Optional[str]:
        if not self._sorted_keys:
            return None
        h = self._hash(key)
        import bisect
        idx = bisect.bisect(self._sorted_keys, h) % len(self._sorted_keys)
        return self._ring[self._sorted_keys[idx]]
