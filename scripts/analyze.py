#!/usr/bin/env python3
"""Parses experiment logs and produces performance plots and summaries.

Reads the `summary.json` manifest (and, where present, the per-repetition
`*.raw.jsonl` request records) written by `scripts/run_experiment.py` under
one results directory, computes the metrics defined below, and writes
labelled PNG plots plus a CSV summary table. Every number here comes from
real HTTP responses recorded by `run_experiment.py` -- nothing in this
module is mocked, simulated, or hand-entered.

Metric definitions (must match exactly, see also module docstring of
scripts/run_experiment.py:summarize):

  cache_hit_ratio       = (local_hits + peer_hits) / (local_hits + peer_hits + origin_fetches)
                          Reported as undefined (None) when that denominator is 0.
  origin_request_reduction_pct(policy)
                        = (baseline_origin_fetches - policy_origin_fetches) / baseline_origin_fetches * 100
                          where baseline is the "none" (no-caching) policy's mean origin_fetches
                          in the SAME experiment run. Reported as undefined when no "none"
                          baseline is present in the run, or when baseline_origin_fetches == 0.
  mean/median/p95/p99 latency_ms
                        = computed over successful (non-error, non-timeout) requests' wall-clock
                          client-observed latency_ms (time from request send to response read
                          completion in workload_generator._http_get) -- this is client-observed
                          latency, not the server's own X-Response-Time-Ms.
  throughput_rps        = total measured requests / wall-clock duration of the measured phase.
  error_rate/timeout_rate = failed or timed-out requests / total measured requests.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402


def load_summary(results_dir: Path) -> dict:
    path = results_dir / "summary.json"
    if not path.is_file():
        raise FileNotFoundError(f"No summary.json found under {results_dir}")
    return json.loads(path.read_text())


def summaries_to_frame(manifest: dict) -> pd.DataFrame:
    df = pd.DataFrame(manifest["summaries"])
    if df.empty:
        raise ValueError("summary.json contains no repetition summaries to analyze")
    return df


def aggregate_by_policy(df: pd.DataFrame) -> pd.DataFrame:
    metrics = [
        "cache_hit_ratio", "mean_latency_ms", "median_latency_ms",
        "p95_latency_ms", "p99_latency_ms", "throughput_rps",
        "error_rate", "timeout_rate",
    ]
    agg = df.groupby("policy")[metrics].agg(["mean", "std"])
    agg.columns = [f"{m}_{stat}" for m, stat in agg.columns]
    agg["repetitions"] = df.groupby("policy").size()
    return agg.reset_index()


def compute_origin_reduction(agg: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """Origin-request-reduction vs. the 'none' (no-caching) baseline, when
    present in this run. Returns NaN (not 0) when undefined."""
    origin_fetch_means = df.groupby("policy")["origin_fetches"].mean()
    result = agg.copy()
    if "none" not in origin_fetch_means.index or origin_fetch_means["none"] == 0:
        result["origin_request_reduction_pct"] = float("nan")
        return result
    baseline = origin_fetch_means["none"]
    result["origin_request_reduction_pct"] = result["policy"].map(
        lambda p: round((baseline - origin_fetch_means.get(p, baseline)) / baseline * 100.0, 2)
    )
    return result


def _bar_plot(agg: pd.DataFrame, value_col: str, err_col: Optional[str], title: str,
              ylabel: str, out_path: Path) -> bool:
    values = agg[value_col]
    if values.isna().all():
        print(f"[analyze] Skipping '{title}': no data available for {value_col}")
        return False
    fig, ax = plt.subplots(figsize=(7, 4.5))
    yerr = agg[err_col].fillna(0) if err_col and err_col in agg.columns else None
    ax.bar(agg["policy"], values.fillna(0), yerr=yerr, capsize=4, color="#3B82F6")
    ax.set_title(title)
    ax.set_xlabel("Cache Policy")
    ax.set_ylabel(ylabel)
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"[analyze] Wrote {out_path}")
    return True


def _grouped_bar_plot(agg: pd.DataFrame, cols: List[str], labels: List[str], title: str,
                       ylabel: str, out_path: Path) -> bool:
    if all(agg[c].isna().all() for c in cols):
        print(f"[analyze] Skipping '{title}': no data available")
        return False
    fig, ax = plt.subplots(figsize=(7, 4.5))
    width = 0.8 / len(cols)
    x = range(len(agg))
    for i, (col, label) in enumerate(zip(cols, labels)):
        ax.bar([xi + i * width for xi in x], agg[col].fillna(0), width=width, label=label)
    ax.set_xticks([xi + width * (len(cols) - 1) / 2 for xi in x])
    ax.set_xticklabels(agg["policy"])
    ax.set_title(title)
    ax.set_xlabel("Cache Policy")
    ax.set_ylabel(ylabel)
    ax.legend()
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"[analyze] Wrote {out_path}")
    return True


def _latency_boxplot(results_dir: Path, policies: List[str], out_path: Path) -> bool:
    data: Dict[str, List[float]] = {}
    for raw_file in results_dir.glob("*.raw.jsonl"):
        policy = None
        for p in policies:
            if f"-{p}-" in raw_file.name:
                policy = p
                break
        if policy is None:
            continue
        latencies = []
        with raw_file.open() as f:
            for line in f:
                rec = json.loads(line)
                if not rec.get("is_warmup") and rec.get("outcome") not in ("error", "timeout"):
                    latencies.append(rec["latency_ms"])
        data.setdefault(policy, []).extend(latencies)

    if not data:
        print("[analyze] Skipping latency distribution plot: no raw .jsonl files found")
        return False

    fig, ax = plt.subplots(figsize=(7, 4.5))
    labels = list(data.keys())
    try:
        ax.boxplot([data[k] for k in labels], tick_labels=labels, showfliers=False)
    except TypeError:
        ax.boxplot([data[k] for k in labels], labels=labels, showfliers=False)
    ax.set_title("Per-Request Latency Distribution by Policy")
    ax.set_xlabel("Cache Policy")
    ax.set_ylabel("Client-Observed Latency (ms)")
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"[analyze] Wrote {out_path}")
    return True


def analyze(results_dir: str, output_dir: Optional[str] = None) -> Path:
    results_path = Path(results_dir)
    manifest = load_summary(results_path)
    df = summaries_to_frame(manifest)

    out_dir = Path(output_dir) if output_dir else results_path / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)

    agg = aggregate_by_policy(df)
    agg = compute_origin_reduction(agg, df)
    agg.to_csv(out_dir / "summary_table.csv", index=False)
    print(f"[analyze] Wrote {out_dir / 'summary_table.csv'}")

    _bar_plot(agg, "cache_hit_ratio_mean", "cache_hit_ratio_std",
              "Cache Hit Ratio by Policy", "Hit Ratio (local+peer / eligible)",
              out_dir / "hit_ratio_by_policy.png")

    _bar_plot(agg, "mean_latency_ms_mean", "mean_latency_ms_std",
              "Mean Response Latency by Policy", "Latency (ms)",
              out_dir / "mean_latency_by_policy.png")

    _grouped_bar_plot(agg, ["p95_latency_ms_mean", "p99_latency_ms_mean"], ["P95", "P99"],
                       "Tail Latency by Policy", "Latency (ms)",
                       out_dir / "tail_latency_by_policy.png")

    _bar_plot(agg, "throughput_rps_mean", "throughput_rps_std",
              "Throughput by Policy", "Requests / sec",
              out_dir / "throughput_by_policy.png")

    if "origin_request_reduction_pct" in agg.columns and not agg["origin_request_reduction_pct"].isna().all():
        _bar_plot(agg, "origin_request_reduction_pct", None,
                  "Origin Request Reduction vs. No-Caching Baseline", "Reduction (%)",
                  out_dir / "origin_reduction_by_policy.png")
    else:
        print("[analyze] Skipping origin-reduction plot: no 'none' (no-caching) baseline "
              "present in this run's cache.policies -- add 'none' to compare against it.")

    _grouped_bar_plot(agg, ["error_rate_mean", "timeout_rate_mean"], ["Error rate", "Timeout rate"],
                       "Reliability by Policy", "Rate",
                       out_dir / "reliability_by_policy.png")

    _latency_boxplot(results_path, manifest.get("policies", []), out_dir / "latency_distribution_by_policy.png")

    print("\n[analyze] All reported metrics are measured directly from real HTTP "
          "responses produced by scripts/run_experiment.py; none are mocked or simulated.")
    print(agg.to_string(index=False))
    return out_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze edge-caching experiment results")
    parser.add_argument("--results-dir", required=True, help="Directory produced by scripts/run_experiment.py")
    parser.add_argument("--output-dir", default=None, help="Where to write plots/CSV (default: <results-dir>/analysis)")
    args = parser.parse_args()
    analyze(args.results_dir, args.output_dir)


if __name__ == "__main__":
    main()
