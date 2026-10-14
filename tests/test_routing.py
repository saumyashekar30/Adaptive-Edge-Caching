"""Unit tests for routing logic, scoring, weights configuration, and node selection."""

import unittest
from pathlib import Path

from src.common.config import load_routing_weights
from src.common.models import NodeMetrics, NodeScore, RoutingWeights
from src.routing.controller import TrafficManagementController
from src.routing.scoring import RoutingScorer, compute_node_score
from src.routing.selector import NodeSelector
from src.telemetry.metrics import MockTelemetryGenerator


class TestRoutingScoring(unittest.TestCase):
    """Tests for multi-factor node scoring formula: α·Latency + β·PacketLoss + γ·Utilization + δ·QueueingDelay."""

    def test_01_exact_default_scoring_formula(self):
        """Verify mathematical calculation with default weights: 0.40*L + 0.30*P + 0.20*U + 0.10*Q."""
        weights = RoutingWeights(alpha=0.40, beta=0.30, gamma=0.20, delta=0.10)
        scorer = RoutingScorer(weights=weights)

        metrics = NodeMetrics(
            node_id="node-1",
            latency_ms=50.0,        # 0.40 * 50 = 20.0
            packet_loss_pct=2.0,    # 0.30 * 2  = 0.6
            utilization_pct=60.0,   # 0.20 * 60 = 12.0
            queueing_delay_ms=10.0, # 0.10 * 10 = 1.0
        )
        expected_score = (0.40 * 50.0) + (0.30 * 2.0) + (0.20 * 60.0) + (0.10 * 10.0)  # 33.6
        node_score = scorer.calculate_score(metrics)

        self.assertAlmostEqual(node_score.score, expected_score, places=4)
        self.assertTrue(node_score.is_eligible)
        self.assertAlmostEqual(node_score.breakdown["latency"], 20.0, places=4)
        self.assertAlmostEqual(node_score.breakdown["packet_loss"], 0.6, places=4)
        self.assertAlmostEqual(node_score.breakdown["utilization"], 12.0, places=4)
        self.assertAlmostEqual(node_score.breakdown["queueing_delay"], 1.0, places=4)

    def test_02_convenience_helper(self):
        """Test compute_node_score helper."""
        w = RoutingWeights(alpha=0.5, beta=0.5, gamma=0.0, delta=0.0)
        score = compute_node_score(latency_ms=20.0, packet_loss_pct=4.0, utilization_pct=50.0, queueing_delay_ms=5.0, weights=w)
        self.assertAlmostEqual(score, (0.5 * 20.0) + (0.5 * 4.0), places=4)

    def test_03_custom_and_dynamic_weights(self):
        """Verify dynamic weight updates alter the score appropriately."""
        scorer = RoutingScorer(weights=RoutingWeights(alpha=1.0, beta=0.0, gamma=0.0, delta=0.0))
        metrics = NodeMetrics(node_id="node-2", latency_ms=45.0, packet_loss_pct=10.0, utilization_pct=80.0, queueing_delay_ms=20.0)

        # Only latency should matter
        score1 = scorer.calculate_score(metrics)
        self.assertAlmostEqual(score1.score, 45.0)

        # Update weights to be packet-loss dominant
        scorer.update_weights(alpha=0.0, beta=2.0)
        score2 = scorer.calculate_score(metrics)
        self.assertAlmostEqual(score2.score, 20.0)

    def test_04_weights_from_config(self):
        """Verify weights loaded from config file."""
        cfg_path = Path("config/routing.yaml")
        if cfg_path.is_file():
            loaded_weights = load_routing_weights(cfg_path)
            self.assertIsInstance(loaded_weights, RoutingWeights)
            scorer = RoutingScorer(config_path=cfg_path)
            self.assertEqual(scorer.weights.alpha, loaded_weights.alpha)

    def test_05_offline_and_unhealthy_nodes_ineligible(self):
        """Nodes marked OFFLINE or DEAD must receive infinite score and be marked ineligible."""
        scorer = RoutingScorer()
        for bad_status in ["OFFLINE", "DEAD", "UNHEALTHY", "UNAVAILABLE"]:
            m = NodeMetrics(node_id="node-bad", status=bad_status, latency_ms=10.0)
            res = scorer.calculate_score(m)
            self.assertEqual(res.score, float("inf"))
            self.assertFalse(res.is_eligible)

    def test_06_degraded_node_penalty(self):
        """Degraded node remains eligible but receives penalty factor."""
        scorer = RoutingScorer(weights=RoutingWeights(alpha=1.0, beta=0.0, gamma=0.0, delta=0.0))
        normal = NodeMetrics(node_id="n1", latency_ms=20.0, status="HEALTHY")
        degraded = NodeMetrics(node_id="n2", latency_ms=20.0, status="DEGRADED")

        score_normal = scorer.calculate_score(normal)
        score_degraded = scorer.calculate_score(degraded)

        self.assertTrue(score_normal.is_eligible)
        self.assertTrue(score_degraded.is_eligible)
        self.assertGreater(score_degraded.score, score_normal.score)


class TestNodeSelector(unittest.TestCase):
    """Tests for selecting the optimal edge node from candidate scores."""

    def setUp(self):
        self.scorer = RoutingScorer(weights=RoutingWeights(alpha=0.4, beta=0.3, gamma=0.2, delta=0.1))
        self.selector = NodeSelector(scorer=self.scorer, fallback_enabled=True)

    def test_01_select_lowest_penalty_score(self):
        """Selector must choose node with the minimal penalty score."""
        candidates = {
            "node-fast": NodeMetrics(node_id="node-fast", latency_ms=10.0, packet_loss_pct=0.0, utilization_pct=20.0, queueing_delay_ms=1.0),
            "node-medium": NodeMetrics(node_id="node-medium", latency_ms=40.0, packet_loss_pct=1.0, utilization_pct=50.0, queueing_delay_ms=5.0),
            "node-slow": NodeMetrics(node_id="node-slow", latency_ms=120.0, packet_loss_pct=5.0, utilization_pct=80.0, queueing_delay_ms=15.0),
        }
        best = self.selector.select_best_node(candidates)
        self.assertEqual(best, "node-fast")

    def test_02_filter_inactive_nodes(self):
        """Selector must respect active_node_ids set from heartbeat monitor."""
        candidates = {
            "node-fast": NodeMetrics(node_id="node-fast", latency_ms=10.0),
            "node-medium": NodeMetrics(node_id="node-medium", latency_ms=30.0),
        }
        # If node-fast is dead/inactive
        active_set = {"node-medium"}
        best = self.selector.select_best_node(candidates, active_node_ids=active_set)
        self.assertEqual(best, "node-medium")

    def test_03_tie_breaking(self):
        """Verify deterministic tie-breaking when two nodes have identical scores."""
        candidates = {
            "node-b": NodeMetrics(node_id="node-b", latency_ms=20.0, packet_loss_pct=0.0, utilization_pct=30.0, queueing_delay_ms=2.0),
            "node-a": NodeMetrics(node_id="node-a", latency_ms=20.0, packet_loss_pct=0.0, utilization_pct=30.0, queueing_delay_ms=2.0),
        }
        # Alphabetic node_id tie breaker
        best = self.selector.select_best_node(candidates)
        self.assertEqual(best, "node-a")

    def test_04_fallback_to_degraded(self):
        """When all healthy nodes are dead, fallback to degraded node if available."""
        candidates = {
            "node-dead": NodeMetrics(node_id="node-dead", status="OFFLINE"),
            "node-degraded": NodeMetrics(node_id="node-degraded", status="DEGRADED", latency_ms=30.0),
        }
        best = self.selector.select_best_node(candidates)
        self.assertEqual(best, "node-degraded")

    def test_05_no_available_nodes(self):
        """When no nodes are available, return None."""
        candidates = {
            "node-dead-1": NodeMetrics(node_id="node-dead-1", status="OFFLINE"),
            "node-dead-2": NodeMetrics(node_id="node-dead-2", status="OFFLINE"),
        }
        best = self.selector.select_best_node(candidates)
        self.assertIsNone(best)

    def test_06_top_k_and_ranking(self):
        """Verify top-k and rank_all ordering."""
        candidates = {
            "n1": NodeMetrics(node_id="n1", latency_ms=50.0),
            "n2": NodeMetrics(node_id="n2", latency_ms=10.0),
            "n3": NodeMetrics(node_id="n3", latency_ms=30.0),
        }
        top2 = self.selector.select_top_k(candidates, k=2)
        self.assertEqual(top2, ["n2", "n3"])

        ranked = self.selector.rank_all(candidates)
        self.assertEqual([nid for nid, _ in ranked], ["n2", "n3", "n1"])


class TestTrafficControllerLogic(unittest.TestCase):
    """Tests for Traffic Management Controller logic and mock telemetry coordination."""

    def test_01_controller_node_registration_and_selection(self):
        controller = TrafficManagementController(use_mock_telemetry=True)
        controller.register_node("edge-1", "http://127.0.0.1:8081")
        controller.register_node("edge-2", "http://127.0.0.1:8082")

        # Manually assign telemetry
        controller.record_telemetry(NodeMetrics(node_id="edge-1", latency_ms=80.0))
        controller.record_telemetry(NodeMetrics(node_id="edge-2", latency_ms=15.0))

        selection = controller.select_best_node()
        self.assertIsNotNone(selection)
        best_id, best_score = selection
        self.assertEqual(best_id, "edge-2")
        self.assertLess(best_score, 10.0)

    def test_02_dynamic_telemetry_shift(self):
        """Verify that when network conditions worsen on edge-2, controller shifts to edge-1."""
        controller = TrafficManagementController(use_mock_telemetry=True)
        controller.register_node("edge-1", "http://127.0.0.1:8081")
        controller.register_node("edge-2", "http://127.0.0.1:8082")

        # Initially edge-2 is better
        controller.record_telemetry(NodeMetrics(node_id="edge-1", latency_ms=30.0, packet_loss_pct=0.0))
        controller.record_telemetry(NodeMetrics(node_id="edge-2", latency_ms=10.0, packet_loss_pct=0.0))
        self.assertEqual(controller.select_best_node()[0], "edge-2")

        # Edge-2 suffers high packet loss and latency spike
        controller.record_telemetry(NodeMetrics(node_id="edge-2", latency_ms=150.0, packet_loss_pct=15.0))
        self.assertEqual(controller.select_best_node()[0], "edge-1")


if __name__ == "__main__":
    unittest.main()
