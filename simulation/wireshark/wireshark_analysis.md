# Wireshark PCAP Packet Analysis & Validation Notes

**Adaptive Edge Caching Protocol & Traffic Validation**  
**Author:** Saumya Shekhar (Modules 5, 6, 8, 9)

---

## 1. Overview of Generated Captures

This directory contains 4 binary `.pcap` capture files validating key network transactions in the Adaptive Edge Caching cluster:

| Capture File | Description | Key Protocol Invariants Validated |
|---|---|---|
| [`cache_hit_edge_serve.pcap`](cache_hit_edge_serve.pcap) | Direct edge cache hit serving client | 3-way handshake, HTTP 200 with `X-Cache: HIT`, sub-millisecond edge latency (`0.42ms`). |
| [`cache_miss_origin_fetch.pcap`](cache_miss_origin_fetch.pcap) | Cold cache miss forwarded to authoritative origin | Client-to-Proxy HTTP GET, upstream TCP socket open to origin, 60ms origin latency, proxy population, `X-Cache: MISS`. |
| [`peer_cache_fetch.pcap`](peer_cache_fetch.pcap) | Inter-edge peer-to-peer cache fetch | Local miss on Node 2 triggers `GET /peer-cache/{id}` to Node 1, Node 1 responds with `X-Peer-Cache: HIT`, client served with `X-Cache: PEER_HIT` without contacting origin. |
| [`node_failure_retransmission_reroute.pcap`](node_failure_retransmission_reroute.pcap) | Node crash, TCP SYN retransmissions, failover reroute | Silent node fails to ACK, TCP SYN retransmission backoff, RST termination, Traffic Controller reroutes client session to Node 1. |

---

## 2. Frame-by-Frame Trace Breakdown

### Capture 1: `cache_hit_edge_serve.pcap`
- **Frame 1 (T = 0.000000s):** `Client (10.0.0.10:51000) -> EdgeNode1 (10.0.1.10:8081) [TCP SYN] Seq=1000 Win=64240 Len=0`
- **Frame 2 (T = 0.000200s):** `EdgeNode1 -> Client [TCP SYN, ACK] Seq=5000 Ack=1001 Win=64240 Len=0`
- **Frame 3 (T = 0.000300s):** `Client -> EdgeNode1 [TCP ACK] Seq=1001 Ack=5001 Len=0`
- **Frame 4 (T = 0.000400s):** `Client -> EdgeNode1 [HTTP GET /data/stream-101]`
- **Frame 5 (T = 0.000850s):** `EdgeNode1 -> Client [HTTP 200 OK]`
  ```http
  HTTP/1.1 200 OK
  Content-Type: application/octet-stream
  Content-Length: 1024
  X-Cache: HIT
  X-Edge-Node-Id: edge-node-1
  X-Cache-Lookup-Time-Ms: 0.42
  X-Response-Time-Ms: 0.85
  X-Resource-Version: 1
  ETag: "stream-101-v1"
  ```
- **Frame 6 (T = 0.001050s):** `Client -> EdgeNode1 [TCP ACK] Ack=6537`

**Analysis:** Total turn-around time between HTTP GET (Frame 4) and HTTP 200 (Frame 5) is **0.45 milliseconds**. Zero round-trips were initiated upstream.

---

### Capture 2: `cache_miss_origin_fetch.pcap`
- **Frames 1–4:** Client initiates connection to `EdgeNode1` and requests `/data/new-video-505`.
- **Frames 5–7:** `EdgeNode1 (10.0.1.10)` initiates upstream connection to `OriginServer (10.0.100.10:8000)`.
- **Frame 8 (T = 0.030700s):** `EdgeNode1 -> OriginServer [HTTP GET /data/new-video-505]`
- **Frame 9 (T = 0.090700s):** `OriginServer -> EdgeNode1 [HTTP 200 OK]` (60ms simulated processing delay).
  - Contains: `X-Origin-Latency-Ms: 60.10`, Payload: 2048 bytes.
- **Frame 10 (T = 0.091700s):** `EdgeNode1 -> Client [HTTP 200 OK]`
  ```http
  HTTP/1.1 200 OK
  Content-Type: application/octet-stream
  Content-Length: 2048
  X-Cache: MISS
  X-Edge-Node-Id: edge-node-1
  X-Origin-Latency-Ms: 60.10
  X-Response-Time-Ms: 62.15
  ```

**Analysis:** Demonstrates cold cache penalty. Edge Proxy measures origin latency ($60.10\text{ms}$) and passes it in headers while synchronously inserting the object into local cache store.

---

### Capture 3: `peer_cache_fetch.pcap`
- **Frame 1:** `Client -> EdgeNode2 (10.0.2.10:8082) [HTTP GET /data/stream-101]`
- **Frame 2:** `EdgeNode2 -> EdgeNode1 (10.0.1.10:8081) [HTTP GET /peer-cache/stream-101]`
  - Header: `X-Requester-Node-Id: edge-node-2`
- **Frame 3:** `EdgeNode1 -> EdgeNode2 [HTTP 200 OK]`
  ```http
  HTTP/1.1 200 OK
  X-Peer-Cache: HIT
  X-Peer-Node-Id: edge-node-1
  X-Resource-Version: 1
  ```
- **Frame 4:** `EdgeNode2 -> Client [HTTP 200 OK]`
  ```http
  HTTP/1.1 200 OK
  X-Cache: PEER_HIT
  X-Edge-Node-Id: edge-node-2
  X-Peer-Node-Id: edge-node-1
  X-Peer-Cache: HIT
  X-Response-Time-Ms: 2.10
  ```

**Analysis:** Confirms shared seam per `docs/api_contracts.md` Section 5. Sibling edge nodes share cache entries horizontally over LAN in **2.10ms**, bypassing the 60ms WAN origin round-trip entirely.

---

### Capture 4: `node_failure_retransmission_reroute.pcap`
- **Frame 1 (T = 0.000s):** `Client -> EdgeNode2 (10.0.2.10:8082) [TCP SYN] Seq=9000` (Node 2 crashed).
- **Frame 2 (T = 1.000s):** `Client -> EdgeNode2 [TCP Retransmission] Seq=9000` (1st RTO expiry).
- **Frame 3 (T = 3.000s):** `Client -> EdgeNode2 [TCP Retransmission] Seq=9000` (Exponential backoff 2.0s).
- **Frame 4 (T = 3.200s):** `Client -> EdgeNode2 [TCP RST] Seq=9001` (Connection aborted).
- **Frames 5–7 (T = 3.250s):** Client / Traffic Controller establishes connection with backup `EdgeNode1 (10.0.1.10:8081)`.
- **Frame 8 (T = 3.2504s):** `Client -> EdgeNode1 [HTTP GET /data/stream-101] X-Failover-Attempt: 2`
- **Frame 9 (T = 3.2512s):** `EdgeNode1 -> Client [HTTP 200 OK] X-Cache: HIT, X-Selected-Node: edge-node-1`

**Analysis:** Demonstrates resilience under silent node failure. When unacknowledged SYN packets exceed RTO, failover triggers dynamic rerouting to `edge-node-1`, recovering the transaction.

---

## 3. Useful Wireshark Display Filters

```text
# Filter only HTTP traffic with Cache headers
http.response.line contains "X-Cache"

# Filter Peer-to-Peer cache queries
http.request.uri contains "peer-cache" or http.response.line contains "X-Peer-Cache"

# Inspect TCP Retransmissions and Dropouts
tcp.analysis.retransmission or tcp.flags.reset == 1

# Measure HTTP Round-Trip Times
http.time > 0.05
```
