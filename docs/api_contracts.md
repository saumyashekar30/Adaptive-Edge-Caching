# API Contracts

## Edge Node API
- `GET /cache/{key}` - Retrieve cached object or trigger fetch from peer/origin.
- `PUT /cache/{key}` - Store or update cached object.
- `DELETE /cache/{key}` - Invalidate cached object.
- `GET /health` - Health check status.
- `GET /metrics` - Telemetry metrics dump.

## Controller API
- `POST /register` - Register active edge node.
- `POST /heartbeat` - Receive node heartbeat and metrics.
- `GET /route?key={key}` - Query recommended edge node for a request.

## Origin API
- `GET /data/{key}` - Retrieve authoritative object from origin.
