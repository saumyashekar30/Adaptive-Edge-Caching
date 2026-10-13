"""Unit tests for Cache Store and Eviction Policies (LRU, LFU, TTL, Adaptive)."""

import time
import unittest

from src.cache.adaptive import AdaptivePolicy
from src.cache.lfu import LFUPolicy
from src.cache.lru import LRUPolicy
from src.cache.store import CacheStore
from src.cache.ttl import TTLPolicy
from src.common.models import CacheMetadata


class TestCacheStore(unittest.TestCase):
    """Tests for core CacheStore functionality."""

    def test_basic_put_and_get_hit(self):
        store = CacheStore(max_capacity_bytes=10000, max_object_size_bytes=5000, policy=LRUPolicy())
        data = b"Hello Edge Cache"
        inserted = store.put("key1", data, content_type="text/plain", version="1.0")
        self.assertTrue(inserted)

        res = store.get("key1")
        self.assertEqual(res.status, "HIT")
        self.assertIsNotNone(res.entry)
        self.assertEqual(res.entry.value, data)
        self.assertEqual(res.entry.size_bytes, len(data))
        self.assertEqual(store.stats()["hits"], 1)

    def test_get_miss(self):
        store = CacheStore(max_capacity_bytes=10000, max_object_size_bytes=5000)
        res = store.get("nonexistent_key")
        self.assertEqual(res.status, "MISS")
        self.assertIsNone(res.entry)
        self.assertEqual(store.stats()["misses"], 1)

    def test_object_size_limit_rejection(self):
        store = CacheStore(max_capacity_bytes=10000, max_object_size_bytes=100)
        large_payload = b"X" * 150  # Exceeds max_object_size_bytes
        inserted = store.put("too_large", large_payload)
        self.assertFalse(inserted)
        self.assertEqual(store.object_count, 0)

        # Smaller payload should succeed
        small_payload = b"X" * 50
        inserted_small = store.put("small", small_payload)
        self.assertTrue(inserted_small)
        self.assertEqual(store.object_count, 1)

    def test_ttl_expiry(self):
        store = CacheStore(max_capacity_bytes=10000, default_ttl_seconds=0.1)
        store.put("expiring_key", b"Temp Data", ttl=0.05)

        # Immediate get -> HIT
        res_initial = store.get("expiring_key")
        self.assertEqual(res_initial.status, "HIT")

        # Wait past TTL
        time.sleep(0.08)

        # Next get -> EXPIRED
        res_expired = store.get("expiring_key")
        self.assertEqual(res_expired.status, "EXPIRED")
        self.assertEqual(store.stats()["expirations"], 1)

    def test_invalidate_and_clear(self):
        store = CacheStore(max_capacity_bytes=10000)
        store.put("doc1", b"Data 1")
        store.put("doc2", b"Data 2")
        self.assertEqual(store.object_count, 2)

        store.invalidate("doc1")
        self.assertEqual(store.object_count, 1)
        self.assertEqual(store.get("doc1").status, "MISS")

        store.clear()
        self.assertEqual(store.object_count, 0)
        self.assertEqual(store.current_size_bytes, 0)


class TestEvictionPolicies(unittest.TestCase):
    """Tests verifying LRU, LFU, TTL, and Adaptive eviction behaviors."""

    def test_lru_eviction(self):
        # Capacity fits exactly 2 items of size 100
        store = CacheStore(max_capacity_bytes=200, max_object_size_bytes=150, policy=LRUPolicy())
        store.put("item1", b"A" * 100)
        store.put("item2", b"B" * 100)

        # Access item1 to make it more recently used than item2
        store.get("item1")

        # Insert item3 -> item2 should be evicted (LRU)
        store.put("item3", b"C" * 100)

        self.assertEqual(store.get("item1").status, "HIT")
        self.assertEqual(store.get("item2").status, "MISS")  # Evicted
        self.assertEqual(store.get("item3").status, "HIT")

    def test_lfu_eviction(self):
        store = CacheStore(max_capacity_bytes=200, max_object_size_bytes=150, policy=LFUPolicy())
        store.put("freq_low", b"A" * 100)
        store.put("freq_high", b"B" * 100)

        # Access freq_high multiple times
        store.get("freq_high")
        store.get("freq_high")
        store.get("freq_high")

        # Insert item3 -> freq_low has access_count 1, should be evicted
        store.put("item3", b"C" * 100)

        self.assertEqual(store.get("freq_high").status, "HIT")
        self.assertEqual(store.get("freq_low").status, "MISS")  # Evicted
        self.assertEqual(store.get("item3").status, "HIT")

    def test_ttl_eviction(self):
        store = CacheStore(max_capacity_bytes=200, max_object_size_bytes=150, policy=TTLPolicy())
        store.put("expires_soon", b"A" * 100, ttl=5.0)
        store.put("expires_later", b"B" * 100, ttl=50.0)

        # Insert item3 -> expires_soon should be selected because it has nearest expiration
        store.put("item3", b"C" * 100)

        self.assertEqual(store.get("expires_soon").status, "MISS")  # Evicted
        self.assertEqual(store.get("expires_later").status, "HIT")
        self.assertEqual(store.get("item3").status, "HIT")

    def test_adaptive_eviction(self):
        # Adaptive policy with weighted factors:
        # High latency origin fetch & high access frequency should be preserved over
        # large low-value items.
        policy = AdaptivePolicy(
            weights={
                "frequency": 0.35,
                "recency": 0.20,
                "origin_latency": 0.25,
                "bandwidth_cost": 0.10,
                "size": 0.05,
                "capacity_pressure": 0.05,
            },
            total_capacity_bytes=200,
        )
        store = CacheStore(max_capacity_bytes=200, max_object_size_bytes=150, policy=policy)

        # item_critical: High origin latency (500ms), accessed multiple times
        store.put("item_critical", b"A" * 100, origin_latency_ms=500.0)
        store.get("item_critical")
        store.get("item_critical")

        # item_cheap: Fast origin latency (5ms), accessed once
        store.put("item_cheap", b"B" * 100, origin_latency_ms=5.0)

        # Add item3 -> item_cheap has vastly lower utility, should be evicted!
        store.put("item3", b"C" * 100)

        self.assertEqual(store.get("item_critical").status, "HIT")  # Preserved!
        self.assertEqual(store.get("item_cheap").status, "MISS")     # Evicted!
        self.assertEqual(store.get("item3").status, "HIT")


if __name__ == "__main__":
    unittest.main()
