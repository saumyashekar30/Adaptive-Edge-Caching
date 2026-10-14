.PHONY: test test-unit test-integration run-origin run-proxy clean

test:
	python -m unittest discover tests

test-unit:
	python -m unittest tests.test_cache tests.test_proxy

test-integration:
	python -m unittest tests.integration.test_end_to_end

run-origin:
	python -m src.origin.server --port 8000 --latency-ms 50

run-proxy:
	python -m src.proxy.server --port 8080 --origin http://127.0.0.1:8000 --policy adaptive

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
