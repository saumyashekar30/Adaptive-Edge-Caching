"""Request Handler logic for URL parsing, cache lookup, hit/miss decisions, and origin fetching."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

try:
    from ..cache.store import CacheStore
    from ..common.models import OriginResponse
    from .origin_client import OriginClient
except ImportError:
    from src.cache.store import CacheStore  # type: ignore
    from src.common.models import OriginResponse  # type: ignore
    from src.proxy.origin_client import OriginClient  # type: ignore

logger = logging.getLogger("proxy_request_handler")


class ProxyRequestHandler:
    """Core proxy logic governing cache hits, misses, evictions, and origin retrieval."""

    def __init__(
        self,
        cache_store: CacheStore,
        origin_client: OriginClient,
        node_id: str = "edge-node-1",
        consistency_manager: Optional[Any] = None,
    ) -> None:
        self.cache = cache_store
        self.origin_client = origin_client
        self.node_id = node_id
        self.consistency = consistency_manager

    def normalize_cache_key(self, path: str, query: Dict[str, list[str]]) -> str:
        """Derive a canonical cache key from request path and query parameters."""
        # Clean path: strip trailing slash (except root)
        clean_path = path if path == "/" else path.rstrip("/")
        # Filter out transient query params like cache-bust tokens if needed
        # For now, if query exists, sort query params for determinism
        if query:
            filtered_pairs = []
            for k in sorted(query.keys()):
                # Exclude purely dynamic tracing params if needed
                if k.lower() not in ("_nocache", "_ts"):
                    for v in sorted(query[k]):
                        filtered_pairs.append(f"{k}={v}")
            if filtered_pairs:
                return f"{clean_path}?{'&'.join(filtered_pairs)}"
        return clean_path

    def handle_request(
        self,
        raw_path: str,
        method: str = "GET",
        headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, Dict[str, str], bytes]:
        """Handle incoming HTTP request and return (status_code, headers, body)."""
        start_time = time.perf_counter()
        headers = headers or {}

        parsed_url = urlparse(raw_path)
        path = parsed_url.path
        query = parse_qs(parsed_url.query)

        # Non-GET requests (e.g. POST/PUT to origin) bypass cache
        if method.upper() != "GET":
            return self._bypass_fetch(path, method, headers, query, start_time)

        # Check Cache-Control bypass
        client_cc = headers.get("Cache-Control", "").lower()
        if "no-cache" in client_cc or "no-store" in client_cc:
            logger.debug("Request has Cache-Control: %s -> Bypassing cache", client_cc)
            return self._bypass_fetch(path, method, headers, query, start_time)

        cache_key = self.normalize_cache_key(path, query)

        # 1. Cache Lookup
        lookup_res = self.cache.get(cache_key)
        lookup_ms = lookup_res.lookup_time_ms

        # 2. Check for HIT
        if lookup_res.status == "HIT" and lookup_res.entry is not None:
            cached_entry = lookup_res.entry

            # Consistency validation hook if active
            if self.consistency is not None:
                self.consistency.record_access(cache_key, cached_entry.version)

            resp_headers = {
                "Content-Type": cached_entry.content_type,
                "Content-Length": str(cached_entry.size_bytes),
                "X-Cache": "HIT",
                "X-Edge-Node-Id": self.node_id,
                "X-Cache-Lookup-Time-Ms": f"{lookup_ms:.2f}",
                "X-Response-Time-Ms": f"{(time.perf_counter() - start_time) * 1000.0:.2f}",
                "X-Resource-Version": cached_entry.version,
            }
            if cached_entry.etag:
                resp_headers["ETag"] = cached_entry.etag

            return 200, resp_headers, cached_entry.value

        # 3. Handle MISS (or EXPIRED) -> Forward to Origin
        logger.debug("Cache %s for key '%s' -> Forwarding to origin", lookup_res.status, cache_key)
        origin_resp = self.origin_client.fetch(path=path, headers=headers, query_params={k: v[0] for k, v in query.items()})

        total_time_ms = (time.perf_counter() - start_time) * 1000.0

        if origin_resp.status_code == 200:
            # Extract TTL from origin headers
            ttl_sec = OriginClient.extract_ttl_from_cache_control(
                origin_resp.headers,
                default_ttl=self.cache.default_ttl_seconds
            )

            # Store in cache
            self.cache.put(
                key=cache_key,
                value=origin_resp.body,
                content_type=origin_resp.content_type,
                version=origin_resp.version,
                etag=origin_resp.etag,
                ttl=ttl_sec,
                origin_latency_ms=origin_resp.latency_ms,
                headers=origin_resp.headers,
            )

            # Notify consistency manager if present
            if self.consistency is not None:
                self.consistency.record_refresh(cache_key, origin_resp.version)

            resp_headers = {
                "Content-Type": origin_resp.content_type,
                "Content-Length": str(len(origin_resp.body)),
                "X-Cache": "MISS",
                "X-Edge-Node-Id": self.node_id,
                "X-Cache-Lookup-Time-Ms": f"{lookup_ms:.2f}",
                "X-Origin-Latency-Ms": f"{origin_resp.latency_ms:.2f}",
                "X-Response-Time-Ms": f"{total_time_ms:.2f}",
                "X-Resource-Version": origin_resp.version,
            }
            if origin_resp.etag:
                resp_headers["ETag"] = origin_resp.etag

            return 200, resp_headers, origin_resp.body

        # Error or unexpected status from origin
        resp_headers = {
            "Content-Type": origin_resp.content_type,
            "Content-Length": str(len(origin_resp.body)),
            "X-Cache": "MISS",
            "X-Edge-Node-Id": self.node_id,
            "X-Response-Time-Ms": f"{total_time_ms:.2f}",
        }
        return origin_resp.status_code, resp_headers, origin_resp.body

    def _bypass_fetch(
        self,
        path: str,
        method: str,
        headers: Dict[str, str],
        query: Dict[str, list[str]],
        start_time: float,
    ) -> Tuple[int, Dict[str, str], bytes]:
        flat_query = {k: v[0] for k, v in query.items()} if query else None
        origin_resp = self.origin_client.fetch(path=path, headers=headers, query_params=flat_query)
        total_time_ms = (time.perf_counter() - start_time) * 1000.0

        resp_headers = {
            "Content-Type": origin_resp.content_type,
            "Content-Length": str(len(origin_resp.body)),
            "X-Cache": "BYPASS",
            "X-Edge-Node-Id": self.node_id,
            "X-Origin-Latency-Ms": f"{origin_resp.latency_ms:.2f}",
            "X-Response-Time-Ms": f"{total_time_ms:.2f}",
            "X-Resource-Version": origin_resp.version,
        }
        if origin_resp.etag:
            resp_headers["ETag"] = origin_resp.etag

        return origin_resp.status_code, resp_headers, origin_resp.body
