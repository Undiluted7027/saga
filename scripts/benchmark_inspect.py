"""Measure the static CLI card-refresh path for the POC fixture."""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import sys
import time
from math import ceil


def measure(selector: str, warmup: int, runs: int) -> dict[str, object]:
    """Run static inspection repeatedly and report median and nearest-rank p95."""
    command = [sys.executable, "-m", "saga.cli", "inspect", selector, "--format", "json"]
    for _ in range(warmup):
        subprocess.run(command, capture_output=True, text=True, check=True)
    durations = []
    for _ in range(runs):
        started = time.perf_counter()
        subprocess.run(command, capture_output=True, text=True, check=True)
        durations.append((time.perf_counter() - started) * 1000)
    ordered = sorted(durations)
    p95 = ordered[max(0, ceil(len(ordered) * 0.95) - 1)]
    return {
        "selector": selector,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "warmup_runs": warmup,
        "measured_runs": runs,
        "median_ms": round(statistics.median(durations), 3),
        "p95_ms": round(p95, 3),
    }


def main(argv: list[str] | None = None) -> int:
    """Parse benchmark options and print a machine-readable result."""
    parser = argparse.ArgumentParser()
    parser.add_argument("selector")
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--runs", type=int, default=20)
    args = parser.parse_args(argv)
    if args.warmup < 0 or args.runs < 1:
        parser.error("warmup must be non-negative and runs must be positive")
    print(json.dumps(measure(args.selector, args.warmup, args.runs), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
