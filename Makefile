.PHONY: install test test-unit test-integration run-origin run-proxy run-node run-controller \
	docker-up docker-down docker-logs workload experiment analyze clean

# Run targets from the repository root (where this Makefile lives).

install:
	pip install -r requirements.txt

test:
	python -m pytest tests/ -v

test-unit:
	python -m unittest tests.test_cache tests.test_proxy tests.test_routing

test-integration:
	python -m unittest tests.integration.test_end_to_end tests.integration.test_multi_node tests.integration.test_edge_node

run-origin:
	python -m src.origin.server --port 8000 --latency-ms 50

run-proxy:
	python -m src.proxy.server --port 8080 --origin http://127.0.0.1:8000 --policy adaptive

# Run one standalone EdgeNode from a config file, e.g.:
#   make run-node NODE_CONFIG=config/nodes/node1.yaml
run-node:
	python -m src.edge.node --config $(NODE_CONFIG)

run-controller:
	python -m src.routing.controller --port 9000 --config config/routing.yaml

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f

# Generate a workload against a running target, e.g.:
#   make workload TARGET=http://127.0.0.1:8081 N=200
workload:
	python scripts/workload_generator.py --target-url $(TARGET) --num-requests $(N) --output results/workload.jsonl

# Run a full experiment defined in config/experiments/*.yaml, e.g.:
#   make experiment EXPERIMENT=config/experiments/baseline.yaml
experiment:
	python scripts/run_experiment.py --config $(EXPERIMENT)

# Analyze the most recent experiment's results, e.g.:
#   make analyze RESULTS_DIR=results/<experiment_id>
analyze:
	python scripts/analyze.py --results-dir $(RESULTS_DIR)

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
