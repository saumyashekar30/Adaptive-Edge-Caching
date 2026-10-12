# Adaptive Edge Caching

Adaptive Edge Caching is a distributed, intelligent edge caching architecture designed to optimize content delivery, minimize latency, and intelligently balance cache policies across peer edge nodes.

## Project Structure

```
adaptive-edge-caching/
├── README.md
├── requirements.txt
├── pyproject.toml
├── .gitignore
├── .env.example
├── Makefile
├── docker-compose.yml
├── docker/
│   ├── Dockerfile.node
│   ├── Dockerfile.origin
│   └── Dockerfile.controller
├── config/
│   ├── nodes/
│   ├── policies/
│   └── experiments/
├── src/
│   ├── proxy/
│   ├── cache/
│   ├── edge/
│   ├── routing/
│   ├── telemetry/
│   ├── consistency/
│   ├── origin/
│   └── common/
├── simulation/
│   ├── ns3/
│   ├── scenarios/
│   ├── packet_tracer/
│   ├── wireshark/
│   └── results/
├── scripts/
├── tests/
│   └── integration/
└── docs/
```

## Getting Started

### Installation
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Running Tests
```bash
pytest
```
