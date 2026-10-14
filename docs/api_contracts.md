# API Contracts & Inter-Module Interface Specifications

This document defines the strict API contracts, HTTP endpoints, JSON schemas, and header conventions established by **Immanuel G** for the Adaptive Edge Caching project. These contracts serve as the agreed system seams between Immanuel (Proxy/Cache/Consistency), Riddhi (Edge Layer/Telemetry/Deployment), and Saumya (Routing/Failure Recovery/Simulation).

---

## 1. Edge Proxy Client Interface

Clients communicate directly with Edge Proxy nodes (or via Saumya's Traffic Controller).

### `GET /{resource_path}`
Retrieves a resource from the edge cache or forwards to origin upon a cache miss.

#### Request Headers
| Header | Type | Required | Description |
|---|---|---|---|
| `Accept` | string | Optional | Desired MIME type (e.g. `application/octet-stream`). |
| `Cache-Control` | string | Optional | `no-cache` or `no-store` forces proxy bypass to origin. |
| `If-None-Match` | string | Optional | Client ETag for conditional revalidation. |

#### Response Headers
| Header | Example | Description |
|---|---|---|
| `X-Cache` | `HIT` \| `MISS` \| `BYPASS` | Indicates cache lookup outcome. |
| `X-Edge-Node-Id` | `edge-node-1` | Identifier of the serving edge node. |
| `X-Cache-Lookup-Time-Ms` | `0.45` | Time taken for internal cache lookup in milliseconds. |
| `X-Origin-Latency-Ms` | `62.10` | Latency incurred fetching from origin (present on `MISS`). |
| `X-Response-Time-Ms` | `1.15` | Total edge processing time in milliseconds. |
| `X-Resource-Version` | `2` | Logical resource version served. |
| `ETag` | `"video101-v2"` | Entity tag of the served resource. |
| `Content-Type` | `application/octet-stream` | MIME type of the payload. |
| `Content-Length` | `2048` | Size in bytes. |

#### Status Codes
- `200 OK`: Resource successfully returned.
- `304 Not Modified`: Cached resource matches client `If-None-Match`.
- `404 Not Found`: Resource does not exist at origin.
- `502 Bad Gateway`: Edge proxy failed to connect to upstream origin.

---

## 2. Mock Origin Interface (`src/origin/server.py`)

### `GET /data/{resource_id}`
Fetches raw resource data from origin.

#### Query Parameters
- `latency_ms` (float, optional): Injects artificial delay in milliseconds (overrides origin default).
- `size` (int, optional): Requests an arbitrary synthetic payload size in bytes.

#### Response Headers
- `ETag`: `"\"{resource_id}-v{version}\""`
- `X-Resource-Version`: string (e.g. `"1"`, `"2"`)
- `X-Origin-Latency-Ms`: float
- `Last-Modified`: RFC 1123 HTTP-date string
- `Cache-Control`: `max-age={seconds}`

---

### `POST /update/{resource_id}` or `PUT /data/{resource_id}`
Mutates a resource on the origin, bumping its version and updating its content timestamp.

#### Request Body (JSON, Optional)
```json
{
  "version": 2,
  "size": 2048
}
```

#### Response Body (JSON, `200 OK`)
```json
{
  "resource_id": "video101",
  "version": 2,
  "etag": "\"video101-v2\"",
  "size_bytes": 2048,
  "last_modified": 1776123456.78
}
```

---

### `GET /health`
Origin liveness check.

#### Response Body (JSON, `200 OK`)
```json
{
  "status": "healthy",
  "service": "mock-origin",
  "uptime": 1284.5
}
```

---

### `GET /versions`
Returns a catalog of all known origin resources and their current versions.

---

## 3. Consistency & Invalidation Interface (`src/consistency/`)

### `POST /cache/invalidate`
Dispatched to edge nodes to trigger cache invalidation.

#### Request Body (JSON)
```json
{
  "key": "/data/video101",
  "prefix": "/data/stream-",
  "new_version": "2"
}
```

#### Response Body (JSON, `200 OK`)
```json
{
  "status": "invalidated",
  "key": "/data/video101",
  "success": true,
  "timestamp": 1776123456.89
}
```

---

## 4. Edge Node Telemetry & Health Interfaces (Shared Seam with Riddhi & Saumya)

### `GET /health`
Edge node health check consumed by Saumya's failure detection and heartbeat monitor.

#### Response Body (JSON, `200 OK`)
```json
{
  "status": "healthy",
  "service": "edge-proxy",
  "node_id": "edge-node-1",
  "uptime": 450.2
}
```

---

### `GET /cache/stats`
Edge cache metrics consumed by Riddhi's telemetry engine (`src/telemetry/metrics.py`) and Saumya's scoring engine (`src/routing/scoring.py`).

#### Response Body (JSON, `200 OK`)
```json
{
  "hits": 1420,
  "misses": 180,
  "total_requests": 1600,
  "hit_ratio": 0.8875,
  "evictions": 45,
  "expirations": 12,
  "object_count": 350,
  "current_size_bytes": 35651584,
  "max_capacity_bytes": 52428800,
  "utilization_pct": 68.0,
  "policy": "adaptive"
}
```

---

## 5. Peer-to-Peer Cache Fetch Interface (Shared Seam with Riddhi's `peer_client.py`)

When an edge node misses locally, it queries sibling peer edge nodes before hitting origin.

### `GET /peer-cache/{resource_id}`

#### Request Headers
- `X-Requester-Node-Id`: `edge-node-2`

#### Response Headers
- `X-Peer-Cache`: `HIT` \| `MISS`
- `X-Peer-Node-Id`: `edge-node-1`
- `X-Resource-Version`: `1`

#### Status Codes
- `200 OK`: Peer had object in its local cache; returns payload.
- `404 Not Found`: Peer did not have object (initiating node then proceeds to origin).
