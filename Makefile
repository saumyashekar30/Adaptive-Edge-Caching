.PHONY: help install test lint run-origin run-controller run-node docker-up docker-down

help:
	@echo "Available commands:"
	@echo "  install         Install project dependencies"
	@echo "  test            Run test suite"
	@echo "  run-origin      Start the origin server"
	@echo "  run-controller  Start the routing controller"
	@echo "  run-node        Start an edge node"
	@echo "  docker-up       Start containerized infrastructure"
	@echo "  docker-down     Stop containerized infrastructure"

install:
	pip install -r requirements.txt

test:
	pytest tests/

run-origin:
	python -m src.origin.server

run-controller:
	python -m src.routing.controller

run-node:
	python -m src.edge.node --config config/nodes/node1.yaml

docker-up:
	docker compose up --build

docker-down:
	docker compose down
