#!/usr/bin/env python3
"""Generates synthetic client traffic distributions (e.g. Zipf, Uniform) and
replays them as real HTTP requests against a running edge proxy / routing
controller. Does NOT simulate cache hits -- every record in the output comes
from an actual HTTP response and its `X-Cache` header, exactly as the server
reported it.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common.models import RequestOutcome, RequestRecord, WorkloadConfig  # noqa: E402


def build_zipf_population(unique_objects: int, alpha: float, seed: int) -> List[int]:
    """Return a lookup table mapping a uniform [0,1) draw to an object index
    under a *bounded* Zipf distribution (rank^-alpha, normalized over
    [1, unique_objects]) -- numpy's own `zipf` is unbounded and would need
    rejection sampling to stay within the configured object set, so this
    implements the bounded form directly with only the stdlib `random`."""
    weights = [1.0 / (rank ** alpha) for rank in range(1, unique_objects + 1)]
    total = sum(weights)
    cumulative = []
    running = 0.0
    for w in weights:
        running += w / total
        cumulative.append(running)
    return cumulative


def sample_zipf_index(cumulative: List[float], rng: random.Random) -> int:
    draw = rng.random()
    lo, hi = 0, len(cumulative) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if cumulative[mid] < draw:
            lo = mid + 1
        else:
            hi = mid
    return lo


def generate_request_plan(cfg: WorkloadConfig) -> List[str]:
    """Deterministically generate the ordered list of resource ids a run will
    request, given `cfg.seed` -- the same config always yields the same plan,
    which is what makes an experiment reproducible."""
    rng = random.Random(cfg.seed)
    n = cfg.num_requests
    resource_ids = [f"obj-{i}" for i in range(cfg.unique_objects)]

    if cfg.distribution == "uniform":
        return [rng.choice(resource_ids) for _ in range(n)]

    if cfg.distribution == "zipf":
        cumulative = build_zipf_population(cfg.unique_objects, cfg.zipf_alpha, cfg.seed)
        return [resource_ids[sample_zipf_index(cumulative, rng)] for _ in range(n)]

    if cfg.distribution == "bursty":
        hot_set_size = max(1, cfg.unique_objects // 20)
        hot_set = resource_ids[:hot_set_size]
        plan: List[str] = []
        while len(plan) < n:
            # A burst hammers the current hot set, then the hot set rotates.
            for _ in range(min(cfg.burst_size, n - len(plan))):
                plan.append(rng.choice(hot_set))
            start = rng.randrange(0, cfg.unique_objects)
            hot_set = resource_ids[start:start + hot_set_size] or resource_ids[:hot_set_size]
        return plan

    raise ValueError(f"Unknown distribution: {cfg.distribution}")


def _http_get(url: str, timeout: float) -> tuple[int, dict, float, Optional[str]]:
    start = time.perf_counter()
    try:
        req = Request(url, method="GET")
        with urlopen(req, timeout=timeout) as resp:
            resp.read()
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            headers = {k: v for k, v in resp.headers.items()}
            return resp.status, headers, elapsed_ms, None
    except HTTPError as e:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        headers = {k: v for k, v in e.headers.items()} if e.headers else {}
        return e.code, headers, elapsed_ms, str(e)
    except (URLError, TimeoutError, OSError) as e:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        is_timeout = "timed out" in str(e).lower()
        return 0, {}, elapsed_ms, ("timeout" if is_timeout else str(e))


def _execute_one(experiment_id: str, request_id: int, target_url: str, resource_id: str,
                  timeout: float, is_warmup: bool) -> RequestRecord:
    url = f"{target_url.rstrip('/')}/data/{resource_id}"
    status, headers, elapsed_ms, error = _http_get(url, timeout)
    if error == "timeout":
        outcome = RequestOutcome.TIMEOUT
    elif error is not None and status == 0:
        outcome = RequestOutcome.ERROR
    else:
        outcome = RequestOutcome.from_headers(status, headers)
    return RequestRecord(
        experiment_id=experiment_id,
        request_id=request_id,
        timestamp=time.time(),
        target_url=target_url,
        resource_id=resource_id,
        status_code=status,
        outcome=outcome.value,
        latency_ms=elapsed_ms,
        node_id=headers.get("X-Edge-Node-Id", headers.get("X-Selected-Node", "")),
        error=error,
        is_warmup=is_warmup,
    )


def run_workload(
    target_url: str,
    cfg: WorkloadConfig,
    experiment_id: Optional[str] = None,
) -> List[RequestRecord]:
    """Execute the full workload (warm-up, then measured requests) against
    `target_url` and return every record, warm-up and measured alike, in
    completion order. Callers decide whether to persist/analyze warm-up rows."""
    experiment_id = experiment_id or f"wl-{uuid.uuid4().hex[:8]}"
    records: List[RequestRecord] = []

    if cfg.warmup_requests > 0:
        warmup_plan = generate_request_plan(WorkloadConfig(**{**cfg.to_dict(), "num_requests": cfg.warmup_requests, "seed": cfg.seed - 1}))
        with ThreadPoolExecutor(max_workers=cfg.concurrency) as pool:
            futures = [
                pool.submit(_execute_one, experiment_id, i, target_url, rid, cfg.request_timeout_seconds, True)
                for i, rid in enumerate(warmup_plan)
            ]
            records.extend(f.result() for f in futures)

    plan = generate_request_plan(cfg)
    spacing = (1.0 / cfg.request_rate_per_sec) if cfg.request_rate_per_sec else None

    with ThreadPoolExecutor(max_workers=cfg.concurrency) as pool:
        futures = []
        next_submit_time = time.perf_counter()
        for i, resource_id in enumerate(plan):
            if spacing:
                now = time.perf_counter()
                if now < next_submit_time:
                    time.sleep(next_submit_time - now)
                next_submit_time += spacing
            if cfg.distribution == "bursty" and i > 0 and i % cfg.burst_size == 0:
                time.sleep(cfg.burst_idle_seconds)
            futures.append(pool.submit(
                _execute_one, experiment_id, i, target_url, resource_id,
                cfg.request_timeout_seconds, False,
            ))
        records.extend(f.result() for f in futures)

    return records


def write_jsonl(records: List[RequestRecord], output_path: str) -> None:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r.to_dict()) + "\n")


def _print_summary(records: List[RequestRecord]) -> None:
    measured = [r for r in records if not r.is_warmup]
    counts: dict = {}
    for r in measured:
        counts[r.outcome] = counts.get(r.outcome, 0) + 1
    print(f"[workload_generator] {len(measured)} measured requests "
          f"({len(records) - len(measured)} warm-up): {counts}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Reproducible HTTP workload generator")
    parser.add_argument("--target-url", required=True, help="Base URL of the edge proxy or controller")
    parser.add_argument("--distribution", default="zipf", choices=["zipf", "uniform", "bursty"])
    parser.add_argument("--num-requests", type=int, default=200)
    parser.add_argument("--unique-objects", type=int, default=50)
    parser.add_argument("--zipf-alpha", type=float, default=1.1)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--request-rate", type=float, default=None, help="Target requests/sec (optional)")
    parser.add_argument("--burst-size", type=int, default=20)
    parser.add_argument("--burst-idle-seconds", type=float, default=0.5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--warmup-requests", type=int, default=0)
    parser.add_argument("--request-timeout", type=float, default=5.0)
    parser.add_argument("--output", default="results/workload.jsonl")
    parser.add_argument("--experiment-id", default=None)
    args = parser.parse_args()

    cfg = WorkloadConfig(
        distribution=args.distribution,
        num_requests=args.num_requests,
        unique_objects=args.unique_objects,
        zipf_alpha=args.zipf_alpha,
        concurrency=args.concurrency,
        request_rate_per_sec=args.request_rate,
        burst_size=args.burst_size,
        burst_idle_seconds=args.burst_idle_seconds,
        seed=args.seed,
        warmup_requests=args.warmup_requests,
        request_timeout_seconds=args.request_timeout,
    )

    records = run_workload(args.target_url, cfg, experiment_id=args.experiment_id)
    write_jsonl(records, args.output)
    _print_summary(records)
    print(f"[workload_generator] Raw results written to {args.output}")


if __name__ == "__main__":
    main()
