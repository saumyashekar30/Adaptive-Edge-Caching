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
| **Saumya Shekhar** | **Modules 5, 6, 8, 9: Scoring & Routing, Health/Heartbeats, NS-3 / Packet Tracer Simulation & Testing** | **Modules 5, 6, 8, 9 Completed ✅** |
| **Riddhi Bhaduri** | Modules 3, 4, 10: Multi-Node Runtime (`src/edge/node.py`, `src/edge/registry.py`), Telemetry (`src/telemetry/metrics.py`), Docker Deployment, Workload Generation & Experiment Automation (`scripts/`) | **Completed ✅** |

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
│   │   ├── models.py              # Shared data structures (incl. NodeConfig, ExperimentConfig, RequestOutcome)
│   │   ├── config.py              # YAML loaders + validation (load_node_config, load_experiment_config)
│   │   └── logging_config.py      # JSON Lines structured logging
│   │
│   ├── edge/                      # Edge node runtime (Riddhi)
│   │   ├── node.py                # EdgeNode: composes proxy+cache+peers+telemetry into one runnable instance
│   │   ├── registry.py            # PeerRegistry (validation) + optional ConsistentHashRing
│   │   └── peer_client.py         # Cache-to-cache HTTP fetch on local miss
│   │
│   ├── routing/                   # Routing controller (Saumya)
│   └── telemetry/
│       └── metrics.py             # TelemetryCollector (controller-side, Saumya) + EdgeNodeTelemetry (node-side, Riddhi)
│
├── scripts/                       # Workload generation, experiment automation, analysis (Riddhi)
│   ├── workload_generator.py      # Reproducible Zipf/Uniform/Bursty HTTP traffic generator
│   ├── run_experiment.py          # Self-managed or external experiment runner
│   └── analyze.py                 # Pandas/Matplotlib metrics + plots from real results
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
│       ├── test_end_to_end.py     # Full end-to-end integration test
│       ├── test_multi_node.py     # 3-node cluster, peer fetch, controller failover
│       └── test_edge_node.py      # EdgeNode runtime, PeerRegistry, config validation, experiment pipeline
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
# Routing and scoring formula unit tests (Module 5)
python -m pytest tests/test_routing.py

# Failure detection, silent node removal, and recovery time tests (Module 6)
python -m pytest tests/test_failure_recovery.py

# Multi-node integration test (Miss, Peer-to-Peer, Origin, Failover rerouting) (Module 9)
python -m pytest tests/integration/test_multi_node.py

# Cache unit tests (LRU, LFU, TTL, Adaptive, Capacity, Size limits)
python -m pytest tests/test_cache.py

# Proxy tests (Hit/Miss headers, URL parsing, origin client)
python -m pytest tests/test_proxy.py

# Edge node runtime integration tests (Module 3/4/10)
python -m pytest tests/integration/test_edge_node.py

# Full test suite (61/61 passing: 41 original + 20 edge-node/registry/pipeline tests)
python -m pytest tests/
```

### 3. Run Traffic Management Controller (Module 5)
```bash
python -m src.routing.controller --port 8000 --config config/routing.yaml
```

### 3a. Run a Standalone Edge Node (Module 3/4 — Riddhi)
`src/edge/node.py` composes the existing proxy, cache, consistency manager, peer
client, and node-side telemetry (`src/telemetry/metrics.py::EdgeNodeTelemetry`)
into one independently runnable, independently configurable instance.

```bash
# From a config file (config/nodes/node1.yaml schema: id, host, port, capacity_mb,
# max_object_size_mb, policy [lru|lfu|ttl|adaptive], origin_url, peers, controller_url)
python -m src.edge.node --config config/nodes/node1.yaml

# Env vars override whatever --config sets (used by docker-compose.yml):
# NODE_ID, NODE_HOST, NODE_PORT, ADVERTISE_HOST, ORIGIN_URL, POLICY, CAPACITY_MB,
# MAX_OBJECT_SIZE_MB, PEERS (comma-separated URLs), CONTROLLER_URL, TELEMETRY_INTERVAL_SECONDS
NODE_ID=node2 NODE_PORT=8082 ORIGIN_URL=http://127.0.0.1:8000 \
PEERS=http://127.0.0.1:8081,http://127.0.0.1:8083 python -m src.edge.node
```

Run a 3-node cluster locally (3 separate terminals, after starting the origin):
```bash
python -m src.edge.node --config config/nodes/node1.yaml
python -m src.edge.node --config config/nodes/node2.yaml
python -m src.edge.node --config config/nodes/node3.yaml

curl -s http://127.0.0.1:8081/data/video-1 -D -   # cold MISS -> origin
curl -s http://127.0.0.1:8081/data/video-1 -D -   # warm HIT  -> local cache
curl -s http://127.0.0.1:8082/data/video-1 -D -   # PEER_HIT  -> fetched from node1, origin untouched
```

Each `EdgeNode` pushes a `NodeMetrics` telemetry snapshot (`POST /telemetry`) to
`controller_url` every `telemetry_interval_seconds` and self-registers with it
(`POST /nodes/register`) — the routing controller consumes exactly this shape
to compute `Score = alpha*latency + beta*packet_loss + gamma*utilization + delta*queueing_delay`
and exclude unavailable nodes (see `docs/api_contracts.md` Section 4 and
`src/routing/scoring.py`).

### 3b. Run the Full Local Cluster + Controller
```bash
python -m src.origin.server --port 8000 &
python -m src.routing.controller --port 9000 --config config/routing.yaml &
CONTROLLER_URL=http://127.0.0.1:9000 python -m src.edge.node --config config/nodes/node1.yaml &
CONTROLLER_URL=http://127.0.0.1:9000 python -m src.edge.node --config config/nodes/node2.yaml &
CONTROLLER_URL=http://127.0.0.1:9000 python -m src.edge.node --config config/nodes/node3.yaml &

curl -s http://127.0.0.1:9000/data/video-1 -D -   # routed to the best-scored node
curl -s http://127.0.0.1:9000/nodes               # registered nodes, scores, telemetry
```

### 4. Run Network Simulation Scenarios (Module 8)
```bash
# Run all 7 discrete-event simulation scenarios & generate CSVs/summaries in simulation/results/
python simulation/run_simulation.py

# Apply NS-3 output parameters (delay, loss, bandwidth) to Docker containers via tc netem
bash simulation/apply_tc_netem.sh edge-node-2 eth0 high_latency
```

### 5. Inspect Wireshark PCAPs & Packet Tracer Demo (Module 8)
- **Wireshark Captures (`simulation/wireshark/`):**
  - `cache_hit_edge_serve.pcap`
  - `cache_miss_origin_fetch.pcap`
  - `peer_cache_fetch.pcap`
  - `node_failure_retransmission_reroute.pcap`
  - Detailed analysis notes in `simulation/wireshark/wireshark_analysis.md`
- **Cisco Packet Tracer 8.x Demo (`simulation/packet_tracer/`):**
  - Architecture diagram: `simulation/packet_tracer/topology_diagram.md`
  - Device configurations: `simulation/packet_tracer/cisco_device_configs.txt`
  - Step-by-step setup guide: `simulation/packet_tracer/packet_tracer_setup_guide.md`

### 3c. Docker Deployment (Module 10 — Riddhi)
```bash
docker compose up --build   # or: docker-compose up --build, depending on your Docker install
```
Brings up `origin` (8000), `controller` (9000→8000 internal), and `node1`/`node2`/`node3`
(8081/8082/8083→8080 internal), wired with service-name DNS peers
(`PEERS=http://node2:8080,http://node3:8080`, etc.) and `CONTROLLER_URL=http://controller:8000`.
Each node self-registers with the controller using `ADVERTISE_HOST` (its own
service name) so the controller can reach it over the compose network, which
also corrects the controller's own config-file-based auto-registration
(`config/nodes/*.yaml` still lists `127.0.0.1` hosts, meant for local/non-Docker
runs) once the node's `POST /nodes/register` call lands.

```bash
curl http://127.0.0.1:9000/data/video-1 -D -   # through the controller
curl http://127.0.0.1:8081/cache/stats
docker compose down
```

**Known limitation:** Docker image builds were not verified inside this
development sandbox -- the build step's `pip install` has no network egress
here (DNS resolution fails), so `docker build`/`docker compose up` could not
be exercised end-to-end in this environment. The `Dockerfile.*`/`docker-compose.yml`
syntax and env-var wiring were validated statically; run `docker compose up --build`
in a normal environment with network access to confirm the live deployment.

### 4. Generate a Reproducible Workload (Module 10 — Riddhi)
```bash
python scripts/workload_generator.py --target-url http://127.0.0.1:8081 \
  --distribution zipf --zipf-alpha 1.1 --num-requests 500 --unique-objects 100 \
  --concurrency 8 --warmup-requests 50 --seed 42 --output results/workload.jsonl
```
Sends real HTTP requests and classifies each by its actual `X-Cache` response
header (`local_hit` / `peer_hit` / `origin_fetch` / `bypass` / `error` / `timeout`)
-- cache hits are never simulated. The same `--seed` always produces the same
request sequence.

### 5. Run a Full Experiment (Module 10 — Riddhi)
```bash
python scripts/run_experiment.py --config config/experiments/baseline.yaml --results-dir results
```
Two modes (`mode:` in the experiment YAML):
- `self_managed` (default): spins up an in-process `MockOriginServer` +
  `ProxyServer` per policy/repetition, runs the workload, tears down, repeats.
  No Docker or externally-running services required.
- `external`: points at an already-running deployment via `target_url:` (e.g.
  the Docker Compose cluster above) instead of starting anything itself.

`cache.policies` may include `none` as an honest no-caching baseline: it is
implemented by giving the cache store 1 byte of capacity (so every `put()` is
rejected by the existing size check) rather than inventing a second code path
-- see the comment in `scripts/run_experiment.py::_run_self_managed_one`.

Output per run: `results/<run_id>/summary.json` (machine-readable, includes
full experiment parameters for reproducibility) and one `*.raw.jsonl` per
policy/repetition with every individual request record, warm-up and
failed/timed-out requests included (never silently dropped).

### 6. Analyze Results (Module 10 — Riddhi)
```bash
python scripts/analyze.py --results-dir results/<run_id>
```
Writes `summary_table.csv` and labelled PNG plots (hit ratio, mean/P95/P99
latency, throughput, origin-request-reduction vs. the `none` baseline when
present, error/timeout rate, and a per-policy latency boxplot from the raw
records) under `results/<run_id>/analysis/`. A plot is skipped — never drawn
from absent/undefined data — when its required baseline or data isn't present,
with a printed explanation (e.g. no origin-reduction plot without a `none`
policy in the same run).

---

## 🩺 Troubleshooting

- **`ConfigError: ... 'policy' must be one of ...`** — a `config/nodes/*.yaml`
  or experiment YAML has an invalid value; the error names the exact field
  and file. Fix the YAML; configs are validated before any service starts.
- **Node won't register with the controller in Docker** — check `CONTROLLER_URL`
  and `ADVERTISE_HOST` are set for that node's service in `docker-compose.yml`;
  without `ADVERTISE_HOST`, a node bound to `0.0.0.0` advertises `127.0.0.1`,
  which is unreachable from other containers.
- **`peers:` entry rejected at startup** — a node cannot list its own `host:port`
  as a peer (`PeerRegistryError`/`ConfigError`); this is caught at config-load
  time specifically to prevent self-referential request loops.
- **A request to a down peer takes ~1s instead of hanging** — expected:
  `PeerClient.fetch_from_peers` uses a short per-peer timeout and falls
  through to the next peer/origin; it never blocks indefinitely.
- **`docker build` fails on `pip install`** — see the Docker limitation above;
  this environment has no network egress during the build step.

## ⚠️ Known Limitations

- Docker image builds/deployment are configured and statically validated but
  not live-tested in this sandbox (no network egress during `docker build`).
- `packet_loss_pct` in `NodeMetrics`/`EdgeNodeTelemetry` is always reported as
  `0.0` in ordinary execution -- real IP-layer packet loss is not observable
  from a stdlib HTTP process without raw sockets or `tc`/netem instrumentation
  (see `simulation/apply_tc_netem.sh` for the privileged alternative). Never
  fabricated as a nonzero value.
- `queueing_delay_ms` is estimated as a small fraction of measured latency
  (no real OS socket-queue depth is observable from user space); flagged as
  such in `src/telemetry/metrics.py`'s module docstring.
- `utilization_pct` reflects process/system CPU (via `psutil`), not network
  link/interface utilization, which is unavailable without privileged
  instrumentation.
- The `none` (no-caching) experiment baseline is implemented via a
  1-byte-capacity cache store rather than a dedicated code path in
  `src/cache/`/`src/proxy/` -- documented where it's implemented.
- `ProxyServer`'s `policy_name` previously silently fell back to LRU for
  `lfu`/`ttl`; this was a real integration bug (not a design choice) fixed in
  `src/proxy/server.py` as part of this work -- see Git history for the exact
  diff.

---

## 📊 End-to-End Validation Results

- **Cold MISS Latency:** ~83 ms (forwarded to origin with simulated delay)
- **Warm HIT Latency:** ~1.1 ms (served from edge in-memory cache)
- **Speedup:** **~75x reduction** in client retrieval latency
- **Consistency Convergence:** Invalidation events converge in under 80ms with 0 stale hits served.
- **Adaptive Policy:** Successfully prioritizes high-latency/high-frequency assets under capacity limits.
