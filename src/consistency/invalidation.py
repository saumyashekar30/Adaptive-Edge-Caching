"""Consistency and Invalidation Engine for Edge Caches.

Features:
- Resource version metadata management (versions, ETags, timestamps)
- Invalidation events (targeted, prefix, or TTL-driven)
- Proactive refresh vs on-demand revalidation
- Stale-response detection and JSON-line event logging
- Cache convergence-time tracking and measurement
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

try:
    from ..cache.store import CacheStore
    from ..common.models import InvalidationRecord
    from ..proxy.origin_client import OriginClient
except ImportError:
    from src.cache.store import CacheStore  # type: ignore
    from src.common.models import InvalidationRecord  # type: ignore
    from src.proxy.origin_client import OriginClient  # type: ignore

logger = logging.getLogger("consistency_invalidation")


@dataclass
class VersionMetadata:
    """Tracks version status for a specific resource."""
    resource_id: str
    latest_known_version: str = "1.0"
    latest_etag: str = ""
    last_modified_ts: float = field(default_factory=time.time)
    is_invalidated: bool = False
    invalidation_ts: Optional[float] = None
    target_version: Optional[str] = None


class ConsistencyManager:
    """Coordinates version metadata, invalidation dispatch, and convergence telemetry."""

    def __init__(
        self,
        cache_store: Optional[CacheStore] = None,
        origin_client: Optional[OriginClient] = None,
        log_file_path: Optional[str] = None,
    ) -> None:
        self.cache_store = cache_store
        self.origin_client = origin_client
        self.log_file_path = log_file_path

        self._lock = threading.RLock()
        self._versions: Dict[str, VersionMetadata] = {}
        self._invalidation_records: List[InvalidationRecord] = []
        self._stale_hit_count: int = 0
        self._convergence_times: List[float] = []

    def set_cache_store(self, cache_store: CacheStore) -> None:
        with self._lock:
            self.cache_store = cache_store

    def set_origin_client(self, origin_client: OriginClient) -> None:
        with self._lock:
            self.origin_client = origin_client

    def register_resource(
        self,
        resource_id: str,
        version: str = "1.0",
        etag: str = "",
        last_modified: Optional[float] = None,
    ) -> VersionMetadata:
        """Register or update initial version metadata for a resource."""
        with self._lock:
            meta = VersionMetadata(
                resource_id=resource_id,
                latest_known_version=version,
                latest_etag=etag,
                last_modified_ts=last_modified or time.time(),
            )
            self._versions[resource_id] = meta
            return meta

    def invalidate(
        self,
        resource_id: str,
        new_version: Optional[str] = None,
        purge_cache: bool = True,
    ) -> InvalidationRecord:
        """Trigger an invalidation event for a resource.

        Args:
            resource_id: Resource identifier / cache key.
            new_version: Optional anticipated new version string.
            purge_cache: Whether to immediately remove from local CacheStore.

        Returns:
            InvalidationRecord tracking the transition.
        """
        now = time.time()
        with self._lock:
            meta = self._versions.setdefault(resource_id, VersionMetadata(resource_id=resource_id))
            meta.is_invalidated = True
            meta.invalidation_ts = now
            meta.target_version = new_version

            record = InvalidationRecord(
                resource_id=resource_id,
                invalidated_at=now,
                new_version=new_version,
                status="PENDING",
            )
            self._invalidation_records.append(record)

            if purge_cache and self.cache_store is not None:
                self.cache_store.invalidate(resource_id)

            self._log_event({
                "event": "invalidation_triggered",
                "resource_id": resource_id,
                "target_version": new_version,
                "timestamp": now,
                "purged": purge_cache,
            })
            return record

    def record_access(self, resource_id: str, served_version: str) -> None:
        """Check if an access serves stale content while an invalidation is in flight."""
        with self._lock:
            meta = self._versions.get(resource_id)
            if not meta:
                return

            # Check if this access is serving an outdated version
            if meta.is_invalidated or (meta.target_version and meta.target_version != served_version):
                self._stale_hit_count += 1
                for rec in self._invalidation_records:
                    if rec.resource_id == resource_id and rec.status == "PENDING":
                        rec.stale_hits += 1

                self._log_event({
                    "event": "stale_hit_detected",
                    "resource_id": resource_id,
                    "served_version": served_version,
                    "target_version": meta.target_version,
                    "timestamp": time.time(),
                })

    def record_refresh(self, resource_id: str, fresh_version: str) -> Optional[float]:
        """Mark convergence when the new version is successfully acquired."""
        now = time.time()
        with self._lock:
            meta = self._versions.get(resource_id)
            if not meta:
                self._versions[resource_id] = VersionMetadata(
                    resource_id=resource_id,
                    latest_known_version=fresh_version,
                )
                return None

            convergence_ms = 0.0
            if meta.invalidation_ts:
                convergence_ms = (now - meta.invalidation_ts) * 1000.0
                self._convergence_times.append(convergence_ms)

                # Update matching pending records
                for rec in self._invalidation_records:
                    if rec.resource_id == resource_id and rec.status == "PENDING":
                        rec.status = "CONVERGED"
                        rec.convergence_time_ms = convergence_ms

            meta.latest_known_version = fresh_version
            meta.is_invalidated = False
            meta.invalidation_ts = None
            meta.target_version = None

            self._log_event({
                "event": "convergence_completed",
                "resource_id": resource_id,
                "converged_version": fresh_version,
                "convergence_time_ms": round(convergence_ms, 2),
                "timestamp": now,
            })
            return convergence_ms

    def refresh(self, resource_id: str) -> bool:
        """Proactively pull latest version from origin and update cache."""
        if not self.origin_client or not self.cache_store:
            logger.warning("Cannot refresh '%s': origin_client or cache_store missing", resource_id)
            return False

        logger.debug("Proactively refreshing '%s' from origin", resource_id)
        origin_resp = self.origin_client.fetch(path=resource_id)
        if origin_resp.status_code == 200:
            ttl_sec = OriginClient.extract_ttl_from_cache_control(
                origin_resp.headers,
                default_ttl=self.cache_store.default_ttl_seconds
            )
            self.cache_store.put(
                key=resource_id,
                value=origin_resp.body,
                content_type=origin_resp.content_type,
                version=origin_resp.version,
                etag=origin_resp.etag,
                ttl=ttl_sec,
                origin_latency_ms=origin_resp.latency_ms,
                headers=origin_resp.headers,
            )
            self.record_refresh(resource_id, origin_resp.version)
            return True
        return False

    def stats(self) -> Dict[str, Any]:
        """Aggregate consistency and convergence telemetry."""
        with self._lock:
            avg_conv = (
                sum(self._convergence_times) / len(self._convergence_times)
                if self._convergence_times else 0.0
            )
            max_conv = max(self._convergence_times) if self._convergence_times else 0.0
            return {
                "total_invalidations": len(self._invalidation_records),
                "total_stale_hits": self._stale_hit_count,
                "total_converged": len(self._convergence_times),
                "avg_convergence_time_ms": round(avg_conv, 2),
                "max_convergence_time_ms": round(max_conv, 2),
                "pending_invalidations": sum(1 for r in self._invalidation_records if r.status == "PENDING"),
            }

    def _log_event(self, event_data: Dict[str, Any]) -> None:
        """Write structured JSON log entry for consistency events."""
        logger.info("[CONSISTENCY_EVENT] %s", json.dumps(event_data))
        if self.log_file_path:
            try:
                os.makedirs(os.path.dirname(self.log_file_path), exist_ok=True)
                with open(self.log_file_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(event_data) + "\n")
            except Exception as e:
                logger.error("Failed writing consistency log: %s", e)
