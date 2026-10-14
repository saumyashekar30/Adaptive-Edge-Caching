"""Node scoring mechanisms based on latency, packet loss, utilization, and queueing delay.

Formula:
    Score = α·Latency + β·PacketLoss + γ·Utilization + δ·QueueingDelay
    
    where weights α, β, γ, δ are loaded from config.
    A lower score indicates a more desirable, higher-performance edge node.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Union

try:
    from ..common.config import load_routing_weights
    from ..common.models import NodeMetrics, NodeScore, RoutingWeights
except ImportError:
    from src.common.config import load_routing_weights  # type: ignore
    from src.common.models import NodeMetrics, NodeScore, RoutingWeights  # type: ignore

logger = logging.getLogger("routing_scorer")


def compute_node_score(
    latency_ms: float,
    packet_loss_pct: float,
    utilization_pct: float,
    queueing_delay_ms: float,
    weights: Optional[RoutingWeights] = None,
) -> float:
    """Convenience helper to compute penalty score directly from scalar inputs."""
    w = weights or RoutingWeights()
    return (
        w.alpha * latency_ms
        + w.beta * packet_loss_pct
        + w.gamma * utilization_pct
        + w.delta * queueing_delay_ms
    )


class RoutingScorer:
    """Calculates composite utility/penalty score for edge nodes based on telemetry."""

    def __init__(
        self,
        weights: Optional[RoutingWeights] = None,
        config_path: Optional[Union[str, Path]] = None,
    ) -> None:
        if weights is not None:
            self.weights = weights
        elif config_path is not None:
            self.weights = load_routing_weights(config_path)
        else:
            self.weights = load_routing_weights()
        logger.info(
            "RoutingScorer initialized with weights: alpha=%.2f, beta=%.2f, gamma=%.2f, delta=%.2f",
            self.weights.alpha,
            self.weights.beta,
            self.weights.gamma,
            self.weights.delta,
        )

    def update_weights(
        self,
        alpha: Optional[float] = None,
        beta: Optional[float] = None,
        gamma: Optional[float] = None,
        delta: Optional[float] = None,
    ) -> None:
        """Dynamically update scoring coefficients."""
        if alpha is not None:
            self.weights.alpha = float(alpha)
        if beta is not None:
            self.weights.beta = float(beta)
        if gamma is not None:
            self.weights.gamma = float(gamma)
        if delta is not None:
            self.weights.delta = float(delta)
        logger.info(
            "Updated routing weights to alpha=%.2f, beta=%.2f, gamma=%.2f, delta=%.2f",
            self.weights.alpha,
            self.weights.beta,
            self.weights.gamma,
            self.weights.delta,
        )

    def calculate_score(self, metrics: Union[NodeMetrics, Dict[str, Any]]) -> NodeScore:
        """Compute the weighted penalty score for a given node's telemetry."""
        if isinstance(metrics, dict):
            node_metrics = NodeMetrics.from_dict(metrics)
        else:
            node_metrics = metrics

        node_id = node_metrics.node_id
        status_upper = node_metrics.status.upper()

        # Ineligible states: nodes marked dead or offline receive an infinite score
        if status_upper in ("OFFLINE", "DEAD", "UNAVAILABLE", "UNHEALTHY"):
            return NodeScore(
                node_id=node_id,
                score=float("inf"),
                breakdown={
                    "latency": float("inf"),
                    "packet_loss": float("inf"),
                    "utilization": float("inf"),
                    "queueing_delay": float("inf"),
                },
                is_eligible=False,
            )

        latency_comp = self.weights.alpha * max(0.0, node_metrics.latency_ms)
        loss_comp = self.weights.beta * max(0.0, node_metrics.packet_loss_pct)
        util_comp = self.weights.gamma * max(0.0, node_metrics.utilization_pct)
        queue_comp = self.weights.delta * max(0.0, node_metrics.queueing_delay_ms)

        total_score = latency_comp + loss_comp + util_comp + queue_comp

        # If node is degraded, add a penalty factor but keep eligible as fallback
        if status_upper == "DEGRADED":
            total_score += 100.0

        return NodeScore(
            node_id=node_id,
            score=total_score,
            breakdown={
                "latency": latency_comp,
                "packet_loss": loss_comp,
                "utilization": util_comp,
                "queueing_delay": queue_comp,
            },
            is_eligible=True,
        )

    def score_nodes(
        self,
        nodes_metrics: Mapping[str, Union[NodeMetrics, Dict[str, Any]]],
    ) -> Dict[str, NodeScore]:
        """Compute scores for a collection of node telemetry records."""
        scores: Dict[str, NodeScore] = {}
        for node_id, metrics in nodes_metrics.items():
            scores[node_id] = self.calculate_score(metrics)
        return scores
