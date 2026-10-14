"""Edge node selection logic choosing optimal edge nodes based on telemetry scores."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple, Union

try:
    from ..common.models import NodeMetrics, NodeScore
    from .scoring import RoutingScorer
except ImportError:
    from src.common.models import NodeMetrics, NodeScore  # type: ignore
    from src.routing.scoring import RoutingScorer  # type: ignore

logger = logging.getLogger("node_selector")


class NoAvailableNodesError(Exception):
    """Raised when no edge nodes are healthy or eligible to service traffic."""
    pass


class NodeSelector:
    """Selects the best edge node from candidate telemetry and computed scores."""

    def __init__(
        self,
        scorer: Optional[RoutingScorer] = None,
        fallback_enabled: bool = True,
    ) -> None:
        self.scorer = scorer or RoutingScorer()
        self.fallback_enabled = fallback_enabled

    def _normalize_scores(
        self,
        candidates: Mapping[str, Union[NodeScore, NodeMetrics, Dict[str, Any]]],
    ) -> Dict[str, NodeScore]:
        """Convert input metrics or scores into normalized NodeScore objects."""
        normalized: Dict[str, NodeScore] = {}
        for node_id, item in candidates.items():
            if isinstance(item, NodeScore):
                normalized[node_id] = item
            else:
                normalized[node_id] = self.scorer.calculate_score(item)
        return normalized

    def select_best_node(
        self,
        candidates: Mapping[str, Union[NodeScore, NodeMetrics, Dict[str, Any]]],
        active_node_ids: Optional[Set[str]] = None,
    ) -> Optional[str]:
        """Pick the best edge node (lowest score) among active and eligible candidates."""
        if not candidates:
            return None

        scores = self._normalize_scores(candidates)

        # Filter out nodes that heartbeat or health monitor marked inactive
        eligible_nodes: List[Tuple[str, float]] = []
        degraded_nodes: List[Tuple[str, float]] = []

        for node_id, node_score in scores.items():
            if active_node_ids is not None and node_id not in active_node_ids:
                continue
            if not node_score.is_eligible or node_score.score == float("inf"):
                continue

            # Check if degraded
            if node_score.score >= 100.0:
                degraded_nodes.append((node_id, node_score.score))
            else:
                eligible_nodes.append((node_id, node_score.score))

        # Sort by score ascending (lowest penalty score wins)
        # Secondary sort key is node_id for deterministic tie-breaking
        eligible_nodes.sort(key=lambda x: (x[1], x[0]))

        if eligible_nodes:
            best_id, best_score = eligible_nodes[0]
            logger.debug("Selected best node %s with score %.3f", best_id, best_score)
            return best_id

        # Fallback to degraded nodes if enabled
        if self.fallback_enabled and degraded_nodes:
            degraded_nodes.sort(key=lambda x: (x[1], x[0]))
            best_degraded_id, best_degraded_score = degraded_nodes[0]
            logger.warning(
                "No healthy nodes available. Falling back to degraded node %s (score: %.3f)",
                best_degraded_id,
                best_degraded_score,
            )
            return best_degraded_id

        logger.error("No eligible edge nodes found among %d candidates", len(candidates))
        return None

    def select_top_k(
        self,
        candidates: Mapping[str, Union[NodeScore, NodeMetrics, Dict[str, Any]]],
        k: int = 2,
        active_node_ids: Optional[Set[str]] = None,
    ) -> List[str]:
        """Return the top-K best performing edge nodes."""
        if not candidates or k <= 0:
            return []

        scores = self._normalize_scores(candidates)
        eligible = [
            (node_id, ns.score)
            for node_id, ns in scores.items()
            if (active_node_ids is None or node_id in active_node_ids)
            and ns.is_eligible
            and ns.score != float("inf")
        ]
        eligible.sort(key=lambda x: (x[1], x[0]))
        return [node_id for node_id, _ in eligible[:k]]

    def rank_all(
        self,
        candidates: Mapping[str, Union[NodeScore, NodeMetrics, Dict[str, Any]]],
        active_node_ids: Optional[Set[str]] = None,
    ) -> List[Tuple[str, float]]:
        """Return all active nodes ranked from lowest (best) to highest (worst) score."""
        scores = self._normalize_scores(candidates)
        eligible = [
            (node_id, ns.score)
            for node_id, ns in scores.items()
            if (active_node_ids is None or node_id in active_node_ids)
            and ns.is_eligible
            and ns.score != float("inf")
        ]
        eligible.sort(key=lambda x: (x[1], x[0]))
        return eligible
