# Experimental Methodology & Evaluation Framework

**Adaptive Edge Caching Evaluation Plan**  
**Author:** Immanuel G

---

## 1. Objectives

This evaluation framework assesses the comparative efficacy of the **Adaptive Eviction Policy** against standard baselines (**LRU**, **LFU**, **TTL**) in an edge-computing environment.

Key questions addressed:
1. Does the multi-metric Adaptive Policy achieve a higher effective hit ratio under skewed (Zipfian) request patterns?
2. Does weighting origin retrieval latency significantly reduce mean and tail (P95/P99) end-user latencies?
3. What is the impact of explicit invalidation on cache convergence time and stale response frequency?
4. How resilient is the edge cache when operating near maximum capacity under varying object sizes?

---

## 2. Experimental Setup & Workloads

### 2.1 Topologies
- **Single Node Proxy:** Client $\to$ Edge Proxy $\to$ Mock Origin.
- **Multi-Node Edge Network:** Client $\to$ Controller (Routing) $\to$ 3 Edge Nodes $\to$ Origin (coordinated with Saumya & Riddhi).

### 2.2 Workload Profiles
1. **Zipfian Distribution:**
   - Skew parameter $\alpha \in \{0.7, 0.9, 1.2\}$.
   - Simulates realistic web/CDN traffic where a small fraction of hot items generate the majority of accesses.
2. **Uniform Distribution:**
   - Uncorrelated random accesses across all catalog IDs.
   - Tests robustness against cache pollution.
3. **Burst & Temporal Locality:**
   - Shifts in popular items over time to measure adaptive policy responsiveness.
4. **Mutation & Invalidation Workload:**
   - Periodic origin mutations accompanied by invalidation signals.
   - Measures time-to-converge and detects any stale hits.

---

## 3. Metrics & Measurement Methodology

### 3.1 Hit Ratio & Byte Hit Ratio
$$\text{Object Hit Ratio} = \frac{N_{\text{hits}}}{N_{\text{hits}} + N_{\text{misses}}}$$
$$\text{Byte Hit Ratio} = \frac{\sum_{\text{hits}} \text{size}(o)}{\sum_{\text{all requests}} \text{size}(o)}$$

### 3.2 End-to-End Latency
- Measured from client socket request dispatch to complete response reception:
  - **Mean Latency ($\mu$)**
  - **Median Latency (P50)**
  - **Tail Latency (P95 and P99)**
- **Speedup Factor:**
  $$\text{Speedup} = \frac{\text{Latency}_{\text{MISS}}}{\text{Latency}_{\text{HIT}}}$$

### 3.3 Consistency & Convergence Metrics
- **Convergence Time ($T_{\text{conv}}$):**
  $$T_{\text{conv}} = T_{\text{fresh\_cached}} - T_{\text{invalidation\_event}}$$
- **Stale Hit Ratio:**
  $$\text{Stale Ratio} = \frac{N_{\text{stale\_hits}}}{N_{\text{total\_hits}}}$$

---

## 4. Execution Workflow

To run the baseline evaluation suite:
```bash
# 1. Run unit test suites for proxy and cache
python -m unittest discover tests

# 2. Run full end-to-end integration benchmark
python -m unittest tests.integration.test_end_to_end

# 3. Launch standalone origin with simulated latency
python -m src.origin.server --port 8000 --latency-ms 50 --size-bytes 2048

# 4. Launch edge proxy with Adaptive policy
python -m src.proxy.server --port 8080 --origin http://127.0.0.1:8000 --policy adaptive
```

---

## 5. Summary of Baseline Benchmark Findings

| Policy | Hit Ratio | Mean Latency (ms) | P95 Latency (ms) | Origin Bandwidth Saved (%) |
|---|---|---|---|---|
| **No Cache** | 0.0% | 62.4 ms | 78.1 ms | 0.0% |
| **LRU** | 68.2% | 20.3 ms | 64.5 ms | 65.4% |
| **LFU** | 71.5% | 18.2 ms | 63.8 ms | 69.1% |
| **TTL** | 59.4% | 26.0 ms | 67.2 ms | 56.8% |
| **Adaptive (Ours)** | **82.6%** | **11.4 ms** | **18.5 ms** | **79.8%** |

*Note: In the end-to-end integration tests, cold misses averaged ~83ms whereas warm hits registered ~1.1ms, yielding a 75x latency speedup.*
