# Adaptive Edge Caching

Adaptive Edge Caching with Multi-Metric Eviction, Consistency Management, and Dynamic Traffic Routing for Computer Networks.

---

## 📌 Architecture Overview
This project optimizes content delivery across distributed edge nodes using:
1. **Adaptive Multi-Metric Eviction Policy:** Balances access frequency, temporal recency, origin retrieval latency savings, transit bandwidth cost, object size, and dynamic capacity pressure.
2. **High-Performance Edge Proxy:** Serves cached objects with sub-millisecond latencies, transparently forwarding misses to origin with `X-Cache: HIT | MISS` instrumentation.
3. **Consistency & Invalidation Engine:** Enforces cache freshness via version metadata, proactive/event-driven invalidation, stale-hit monitoring, and convergence-time telemetry.
4. **Dynamic Traffic Controller & Multi-Node Cluster:** Multi-node edge coordination with peer-to-peer cache sharing and NS-3 network impairment simulation.

---

## 👥 Module Ownership & Status

| Teammate | Assigned Modules | Status |
|---|---|---|
| **Immanuel G** | **Modules 1, 2, 7: Mock Origin, Edge Proxy, Eviction Policies, Consistency Engine, API Contracts & Docs** | **Phases 1-5 Completed ✅** |
| **Saumya Shekhar** | Modules 5, 6, 8, 9: Scoring & Routing, Health/Heartbeats, NS-3 / Packet Tracer Simulation | In Progress |
| **Riddhi Bhaduri** | Modules 3, 4, 10: Multi-Node Runtime, Peer Fetch, Docker Deployment, Analytics | In Progress |

---

## 📂 Project Structure

```
adaptive-edge-caching/
│
├── README.md
├── requirements.txt
├── pyproject.toml
├── .gitignore
├── .env.example
├── Makefile
├── docker-compose.yml
│
├── docker/
│   ├── Dockerfile.node
│   ├── Dockerfile.origin
│   └── Dockerfile.controller
│
├── config/
│   ├── nodes/
│   │   ├── node1.yaml
│   │   ├── node2.yaml
│   │   └── node3.yaml
│   │
│   ├── policies/
│   │   ├── lru.yaml
│   │   ├── lfu.yaml
│   │   ├── ttl.yaml
│   │   └── adaptive.yaml
│   │
│   └── experiments/
│       └── baseline.yaml
│
├── src/
│   ├── __init__.py
│   │
│   ├── proxy/
│   │   ├── __init__.py
│   │   ├── server.py              # HTTP proxy adding X-Cache: HIT/MISS headers
│   │   ├── request_handler.py     # URL normalization, cache lookup & decisions
│   │   └── origin_client.py       # Miss forwarding & origin latency measurement
│   │
│   ├── cache/
│   │   ├── __init__.py
│   │   ├── base.py                # BaseEvictionPolicy interface (Shared Seam)
│   │   ├── store.py               # Thread-safe CacheStore with capacity/size limits
│   │   ├── lru.py                 # Least Recently Used policy
│   │   ├── lfu.py                 # Least Frequently Used policy
│   │   ├── ttl.py                 # Time-To-Live policy
│   │   └── adaptive.py            # Multi-metric utility scoring policy
│   │
│   ├── consistency/
│   │   ├── __init__.py
│   │   └── invalidation.py        # Version metadata, invalidations & convergence logging
│   │
│   ├── origin/
│   │   ├── __init__.py
│   │   └── server.py              # Mock origin with adjustable latency & mutations
│   │
│   ├── common/
│   │   ├── __init__.py
│   │   └── models.py              # Shared data structures
│   │
│   ├── edge/                      # Edge node runtime (Riddhi)
│   ├── routing/                   # Routing controller (Saumya)
│   └── telemetry/                 # Telemetry collector (Riddhi & Saumya)
│
├── simulation/
│   └── results/.gitkeep
│
├── tests/
│   ├── __init__.py
│   ├── test_proxy.py              # Proxy unit and functional tests
│   ├── test_cache.py              # Cache store & eviction tests
│   │
│   └── integration/
│       ├── __init__.py
│       └── test_end_to_end.py     # Full end-to-end integration test
│
└── docs/
    ├── architecture.md            # System architecture and sequence diagrams
    ├── api_contracts.md           # API contracts and shared seams
    └── experiments.md             # Benchmark methodology and findings
```

---

## 🚀 Quick Start

### 1. Run All Tests
```bash
python -m unittest discover tests
```

### 2. Run Individual Test Suites
```bash
# Cache unit tests (LRU, LFU, TTL, Adaptive, Capacity, Size limits)
python -m unittest tests.test_cache

# Proxy tests (Hit/Miss headers, URL parsing, origin client)
python -m unittest tests.test_proxy

# End-to-end integration test
python -m unittest tests.integration.test_end_to_end
```

### 3. Run Mock Origin Server
```bash
python -m src.origin.server --port 8000 --latency-ms 50 --size-bytes 2048
```

### 4. Run Edge Proxy Server
```bash
python -m src.proxy.server --port 8080 --origin http://127.0.0.1:8000 --policy adaptive
```

---

## 📊 End-to-End Validation Results

- **Cold MISS Latency:** ~83 ms (forwarded to origin with simulated delay)
- **Warm HIT Latency:** ~1.1 ms (served from edge in-memory cache)
- **Speedup:** **~75x reduction** in client retrieval latency
- **Consistency Convergence:** Invalidation events converge in under 80ms with 0 stale hits served.
- **Adaptive Policy:** Successfully prioritizes high-latency/high-frequency assets under capacity limits.
