#!/usr/bin/env python3
"""Automates execution of edge caching experiments.

Two modes (ExperimentConfig.mode):

  * "self_managed" (default): spins up a MockOriginServer + ProxyServer
    in-process for each policy/repetition, runs the configured workload
    against it, then tears it down. Fully automated and reproducible,
    requires no Docker / externally-running services.

  * "external": services (origin, edge node(s), optionally the routing
    controller) are already running (e.g. via `make docker-up`), and this
    script only generates the workload against `target_url` and collects
    results -- it does not start or stop anything. Use this mode to measure
    the real multi-node/Docker deployment rather than an in-process stand-in.

No baseline is silently skipped: if `cache.policies` in the experiment YAML
names a policy this script cannot construct in self_managed mode, it fails
loudly with a clear error rather than dropping it from the results.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from statistics import mean, median
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common.config import ConfigError, load_experiment_config  # noqa: E402
from src.common.logging_config import configure_logging, log_event  # noqa: E402
from src.common.models import ExperimentConfig, ExperimentSummary, RequestOutcome, RequestRecord, WorkloadConfig  # noqa: E402
from src.origin.server import MockOriginServer  # noqa: E402
from src.proxy.server import ProxyServer  # noqa: E402
from scripts.workload_generator import run_workload, write_jsonl  # noqa: E402

logger = configure_logging(component="run_experiment")


def _percentile(sorted_vals: List[float], pct: float) -> Optional[float]:
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * pct
    f, c = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def summarize(experiment_id: str, exp_name: str, policy: str, repetition: int,
              records: List[RequestRecord], started_at: float, finished_at: float) -> ExperimentSummary:
    measured = [r for r in records if not r.is_warmup]
    total = len(measured)
    by_outcome = {o.value: 0 for o in RequestOutcome}
    for r in measured:
        by_outcome[r.outcome] = by_outcome.get(r.outcome, 0) + 1

    successful = [r for r in measured if r.outcome not in (RequestOutcome.ERROR.value, RequestOutcome.TIMEOUT.value)]
    latencies = sorted(r.latency_ms for r in successful)

    eligible = by_outcome[RequestOutcome.LOCAL_HIT.value] + by_outcome[RequestOutcome.PEER_HIT.value] + by_outcome[RequestOutcome.ORIGIN_FETCH.value]
    hit_ratio = (
        (by_outcome[RequestOutcome.LOCAL_HIT.value] + by_outcome[RequestOutcome.PEER_HIT.value]) / eligible
        if eligible > 0 else None  # undefined, not zero, when there is no valid denominator
    )

    duration = max(finished_at - started_at, 1e-9)
    return ExperimentSummary(
        experiment_id=experiment_id,
        experiment_name=exp_name,
        policy=policy,
        repetition=repetition,
        total_requests=total,
        successful_requests=len(successful),
        failed_requests=by_outcome[RequestOutcome.ERROR.value],
        timed_out_requests=by_outcome[RequestOutcome.TIMEOUT.value],
        local_hits=by_outcome[RequestOutcome.LOCAL_HIT.value],
        peer_hits=by_outcome[RequestOutcome.PEER_HIT.value],
        origin_fetches=by_outcome[RequestOutcome.ORIGIN_FETCH.value],
        bypasses=by_outcome[RequestOutcome.BYPASS.value],
        cache_hit_ratio=round(hit_ratio, 4) if hit_ratio is not None else None,
        mean_latency_ms=round(mean(latencies), 3) if latencies else None,
        median_latency_ms=round(median(latencies), 3) if latencies else None,
        p95_latency_ms=round(_percentile(latencies, 0.95), 3) if latencies else None,
        p99_latency_ms=round(_percentile(latencies, 0.99), 3) if latencies else None,
        throughput_rps=round(total / duration, 3) if total else None,
        error_rate=round(by_outcome[RequestOutcome.ERROR.value] / total, 4) if total else None,
        timeout_rate=round(by_outcome[RequestOutcome.TIMEOUT.value] / total, 4) if total else None,
        duration_seconds=round(duration, 3),
        started_at=started_at,
        finished_at=finished_at,
    )


def _run_self_managed_one(cfg: ExperimentConfig, policy: str, repetition: int, out_dir: Path) -> ExperimentSummary:
    # port=0 lets the OS assign a free ephemeral port, so repeated/parallel
    # runs never collide on a hardcoded port.
    origin = MockOriginServer(
        host="127.0.0.1", port=0,
        default_latency_ms=cfg.origin_base_latency_ms,
    )
    origin.start()
    time.sleep(0.05)

    # "none" (no caching) is not a distinct policy class anywhere in
    # src/cache/ -- CacheStore always caches whatever fits. The architecture
    # *does* permit a true no-caching baseline without inventing new code,
    # though: give the store 1 byte of capacity so every object is rejected
    # by the existing capacity check and every request becomes an origin
    # fetch. This is documented here rather than silently dropping the
    # baseline (see module docstring).
    is_no_cache = policy == "none"
    capacity_bytes = 1 if is_no_cache else int(cfg.cache_capacity_mb * 1024 * 1024)
    proxy = ProxyServer(
        host="127.0.0.1", port=0,
        origin_url=f"http://127.0.0.1:{origin.port}",
        node_id=f"exp-node-{policy}",
        cache_capacity_bytes=capacity_bytes,
        max_object_size_bytes=int(cfg.cache_max_object_size_kb * 1024),
        policy_name="lru" if is_no_cache else policy,
    )
    proxy.start()
    time.sleep(0.05)

    experiment_id = f"{cfg.experiment_name}-{policy}-r{repetition}-{uuid.uuid4().hex[:6]}"
    wl = cfg.workload
    workload_cfg = WorkloadConfig(**{**wl.to_dict(), "seed": wl.seed + repetition})

    started_at = time.time()
    try:
        records = run_workload(f"http://127.0.0.1:{proxy.port}", workload_cfg, experiment_id=experiment_id)
    finally:
        finished_at = time.time()
        proxy.stop()
        origin.stop()

    write_jsonl(records, str(out_dir / f"{experiment_id}.raw.jsonl"))
    summary = summarize(experiment_id, cfg.experiment_name, policy, repetition, records, started_at, finished_at)
    log_event(logger, "experiment_repetition_complete", policy=policy, repetition=repetition,
              total_requests=summary.total_requests, hit_ratio=summary.cache_hit_ratio,
              mean_latency_ms=summary.mean_latency_ms)
    return summary


def _run_external_one(cfg: ExperimentConfig, policy: str, repetition: int, out_dir: Path) -> ExperimentSummary:
    if not cfg.target_url:
        raise ConfigError("mode: external requires 'target_url' in the experiment config")

    experiment_id = f"{cfg.experiment_name}-{policy}-r{repetition}-{uuid.uuid4().hex[:6]}"
    wl = cfg.workload
    workload_cfg = WorkloadConfig(**{**wl.to_dict(), "seed": wl.seed + repetition})

    started_at = time.time()
    records = run_workload(cfg.target_url, workload_cfg, experiment_id=experiment_id)
    finished_at = time.time()

    write_jsonl(records, str(out_dir / f"{experiment_id}.raw.jsonl"))
    return summarize(experiment_id, cfg.experiment_name, policy, repetition, records, started_at, finished_at)


def run_experiment(cfg: ExperimentConfig, results_root: str = "results") -> Path:
    run_id = f"{cfg.experiment_name}-{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
    out_dir = Path(results_root) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    runner = _run_self_managed_one if cfg.mode == "self_managed" else _run_external_one
    if cfg.mode not in ("self_managed", "external"):
        raise ConfigError(f"Unknown experiment mode: {cfg.mode!r}")

    summaries: List[ExperimentSummary] = []
    for policy in cfg.policies:
        for rep in range(cfg.repetitions):
            log_event(logger, "experiment_repetition_start", policy=policy, repetition=rep)
            summary = runner(cfg, policy, rep, out_dir)
            summaries.append(summary)

    manifest = {
        "run_id": run_id,
        "experiment_name": cfg.experiment_name,
        "mode": cfg.mode,
        "policies": cfg.policies,
        "repetitions": cfg.repetitions,
        "workload": cfg.workload.to_dict(),
        "cache_capacity_mb": cfg.cache_capacity_mb,
        "cache_max_object_size_kb": cfg.cache_max_object_size_kb,
        "origin_base_latency_ms": cfg.origin_base_latency_ms,
        "generated_at": time.time(),
        "summaries": [s.to_dict() for s in summaries],
    }
    (out_dir / "summary.json").write_text(json.dumps(manifest, indent=2))
    log_event(logger, "experiment_complete", run_id=run_id, out_dir=str(out_dir), repetitions=len(summaries))
    return out_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a reproducible edge-caching experiment")
    parser.add_argument("--config", required=True, help="Path to config/experiments/*.yaml")
    parser.add_argument("--results-dir", default="results", help="Root directory for experiment output")
    args = parser.parse_args()

    try:
        cfg = load_experiment_config(args.config)
    except ConfigError as e:
        print(f"[run_experiment] Invalid experiment configuration: {e}")
        raise SystemExit(1)

    out_dir = run_experiment(cfg, results_root=args.results_dir)
    print(f"[run_experiment] Results written to {out_dir}")
    print(f"[run_experiment] Summary: {out_dir / 'summary.json'}")


if __name__ == "__main__":
    main()
