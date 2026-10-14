"""Client for fetching resources from the upstream origin server on cache misses."""

from __future__ import annotations

import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, Optional

try:
    from ..common.models import OriginResponse
except ImportError:
    from src.common.models import OriginResponse  # type: ignore

logger = logging.getLogger("origin_client")


class OriginClient:
    """HTTP client responsible for forwarding cache misses to the upstream origin."""

    def __init__(self, origin_base_url: str = "http://127.0.0.1:8000", timeout: float = 10.0):
        self.origin_base_url = origin_base_url.rstrip("/")
        self.timeout = timeout

    def fetch(
        self,
        path: str,
        headers: Optional[Dict[str, str]] = None,
        query_params: Optional[Dict[str, str]] = None,
    ) -> OriginResponse:
        """Fetch a resource from origin and record latency and metadata.

        Args:
            path: Target URI path (e.g., '/data/video1' or 'video1').
            headers: Optional HTTP headers to forward (e.g., If-None-Match).
            query_params: Optional query parameters.

        Returns:
            OriginResponse containing body, headers, status_code, and measured latency.
        """
        clean_path = path if path.startswith("/") else f"/{path}"
        url = f"{self.origin_base_url}{clean_path}"

        if query_params:
            query_str = urllib.parse.urlencode(query_params)
            delimiter = "&" if "?" in url else "?"
            url = f"{url}{delimiter}{query_str}"

        req_headers = headers.copy() if headers else {}
        if "User-Agent" not in req_headers:
            req_headers["User-Agent"] = "AdaptiveEdgeProxy/1.0"

        req = urllib.request.Request(url, headers=req_headers, method="GET")

        start_time = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                body = resp.read()
                resp_headers = {k: v for k, v in resp.headers.items()}
                status_code = resp.status

                version = resp_headers.get("X-Resource-Version", "1.0")
                etag = resp_headers.get("ETag", "")
                content_type = resp_headers.get("Content-Type", "application/octet-stream")

                logger.debug(
                    "Origin fetch success: %s (status=%d, latency=%.2fms, size=%d bytes)",
                    url, status_code, elapsed_ms, len(body)
                )

                return OriginResponse(
                    status_code=status_code,
                    body=body,
                    headers=resp_headers,
                    latency_ms=elapsed_ms,
                    version=version,
                    etag=etag,
                    content_type=content_type,
                )

        except urllib.error.HTTPError as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            body = e.read()
            resp_headers = {k: v for k, v in e.headers.items()} if hasattr(e, "headers") else {}
            version = resp_headers.get("X-Resource-Version", "1.0")
            etag = resp_headers.get("ETag", "")
            content_type = resp_headers.get("Content-Type", "text/plain")

            logger.warning(
                "Origin HTTP error: %s (status=%d, latency=%.2fms)",
                url, e.code, elapsed_ms
            )

            return OriginResponse(
                status_code=e.code,
                body=body,
                headers=resp_headers,
                latency_ms=elapsed_ms,
                version=version,
                etag=etag,
                content_type=content_type,
            )

        except urllib.error.URLError as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            logger.error("Origin network error reaching %s: %s", url, str(e))
            return OriginResponse(
                status_code=502,
                body=f"Bad Gateway: Unable to reach origin at {self.origin_base_url}: {e}".encode("utf-8"),
                headers={"Content-Type": "text/plain"},
                latency_ms=elapsed_ms,
                version="error",
                etag="",
                content_type="text/plain",
            )

    @staticmethod
    def extract_ttl_from_cache_control(headers: Dict[str, str], default_ttl: Optional[float] = 60.0) -> Optional[float]:
        """Extract max-age seconds from Cache-Control header if present."""
        cc = headers.get("Cache-Control", "")
        if not cc:
            return default_ttl

        match = re.search(r"max-age=(\d+)", cc, re.IGNORECASE)
        if match:
            return float(match.group(1))

        if "no-cache" in cc.lower() or "no-store" in cc.lower():
            return 0.0

        return default_ttl
