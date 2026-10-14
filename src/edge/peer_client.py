"""Client for inter-node peer cache lookups adhering to docs/api_contracts.md Section 5."""

from __future__ import annotations

import logging
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("peer_client")


class PeerClient:
    """Inter-node peer cache client querying sibling edge nodes on local cache miss."""

    def __init__(self, requester_node_id: str = "edge-node-1", peer_urls: Optional[List[str]] = None) -> None:
        self.requester_node_id = requester_node_id
        self.peer_urls: List[str] = [u.rstrip("/") for u in (peer_urls or [])]

    def add_peer(self, url: str) -> None:
        """Register a peer URL."""
        clean = url.rstrip("/")
        if clean not in self.peer_urls:
            self.peer_urls.append(clean)

    def remove_peer(self, url: str) -> None:
        """Remove a peer URL."""
        clean = url.rstrip("/")
        if clean in self.peer_urls:
            self.peer_urls.remove(clean)

    def fetch_from_peers(
        self,
        resource_id: str,
        timeout: float = 1.0,
    ) -> Optional[Tuple[bytes, Dict[str, str]]]:
        """Query peers in order for resource_id.
        
        Returns (payload, headers) if any peer returns 200 HIT, else None.
        """
        clean_res_id = resource_id.lstrip("/")
        # If resource_id is "data/video-1", use "video-1" or exact identifier
        if clean_res_id.startswith("data/"):
            clean_res_id = clean_res_id[len("data/"):]

        headers = {
            "X-Requester-Node-Id": self.requester_node_id,
            "User-Agent": f"AdaptiveEdge-PeerClient/{self.requester_node_id}",
        }

        for peer_base in self.peer_urls:
            url = f"{peer_base}/peer-cache/{clean_res_id}"
            start_t = time.perf_counter()
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    elapsed_ms = (time.perf_counter() - start_t) * 1000.0
                    peer_cache_header = resp.headers.get("X-Peer-Cache", "")
                    peer_node_id = resp.headers.get("X-Peer-Node-Id", peer_base)

                    if resp.status == 200 and peer_cache_header == "HIT":
                        logger.info(
                            "Peer cache HIT from %s for %s in %.2fms",
                            peer_node_id,
                            clean_res_id,
                            elapsed_ms,
                        )
                        body = resp.read()
                        resp_headers = {k: v for k, v in resp.headers.items()}
                        return body, resp_headers
            except urllib.error.HTTPError as e:
                # 404 is an expected peer miss
                if e.code == 404:
                    logger.debug("Peer miss on %s for %s", peer_base, clean_res_id)
                else:
                    logger.warning("Peer %s returned HTTP error %d: %s", peer_base, e.code, e)
            except Exception as e:
                logger.debug("Failed to connect to peer %s: %s", peer_base, e)

        return None
