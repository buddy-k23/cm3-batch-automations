"""FULL-CONTRACT re-benchmark for the S25-3 DuckDB native-read fast path.

The original ``benchmark_duckdb_compare.py`` (S25-1) measured **counts only** —
it stopped at ``COUNT(*)`` and never materialized the ``differences`` /
``only_in_*`` / ``field_statistics`` payload the real engines build.  Its caveat
#1 therefore flagged the headline ~100× as an *upper bound* and asked S25-3 to
re-measure the **full materialized contract** and confirm the ADR's conservative
~10-30× expectation survives.

This harness does exactly that: it runs the **production**
:class:`~src.comparators.backends.duckdb_backend.DuckDBComparisonBackend.compare`
(native-read fast path, full contract) against the **production**
:class:`~src.comparators.backends.native_backend.NativeComparisonBackend.compare`
(pandas ``FileComparator``, full contract) on the SAME pipe-delimited files, and
reports realized wall-clock speedup + peak memory.  It also asserts the two
backends agree on the materialized payload (counts + diff/only-in lengths) so a
faster-but-wrong run is disqualified.

Unlike the counts-only harness this exercises src/ production code on purpose.
It is still a *script* (not imported by src/) and writes no artifacts.

Run::

    .venv/bin/python scripts/benchmark_duckdb_full_contract.py
    .venv/bin/python scripts/benchmark_duckdb_full_contract.py --quick   # smaller scales
"""
from __future__ import annotations

import argparse
import gc
import os
import sys
import tempfile
import time
import tracemalloc
from dataclasses import dataclass

import psutil

# Make src importable when run as a script from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Reuse the throwaway harness' realistic data generator.
from scripts.benchmark_duckdb_compare import generate_pair  # noqa: E402

from src.comparators.backends.duckdb_backend import (  # noqa: E402
    DuckDBComparisonBackend,
)
from src.comparators.backends.native_backend import (  # noqa: E402
    NativeComparisonBackend,
)

KEYS = ["ACCT_KEY"]


@dataclass
class Measure:
    """One timed engine run: wall seconds, peak MB, and a contract digest."""

    name: str
    wall: float
    peak_mb: float
    digest: dict


def _digest(result: dict) -> dict:
    """Reduce a full result dict to comparable scalars for parity + reporting.

    Captures the materialized payload *sizes* (not just counts) so a backend
    that skipped building ``differences`` / ``only_in_*`` would be caught.

    Args:
        result: A comparison result dict from either backend.

    Returns:
        Dict of contract scalars (counts + materialized-list lengths).
    """
    only1 = result["only_in_file1"]
    only2 = result["only_in_file2"]
    only1_n = len(only1) if hasattr(only1, "__len__") else int(only1)
    only2_n = len(only2) if hasattr(only2, "__len__") else int(only2)
    diffs = result.get("differences", [])
    # Sum the per-row field-diff entries actually materialized (proves the
    # detailed payload was built, not skipped).
    field_entries = sum(len(d.get("differences", {})) for d in diffs)
    return {
        "total_rows_file1": result["total_rows_file1"],
        "total_rows_file2": result["total_rows_file2"],
        "matching_rows": result["matching_rows"],
        "rows_with_differences": result["rows_with_differences"],
        "only_in_file1": only1_n,
        "only_in_file2": only2_n,
        "diff_rows_materialized": len(diffs),
        "field_diff_entries_materialized": field_entries,
        "field_statistics_built": bool(result.get("field_statistics")),
    }


def _time(name: str, fn) -> Measure:
    """Time *fn* once, capturing wall-clock and peak memory (python + RSS)."""
    proc = psutil.Process()
    gc.collect()
    rss_before = proc.memory_info().rss
    tracemalloc.start()
    t0 = time.perf_counter()
    result = fn()
    wall = time.perf_counter() - t0
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_after = proc.memory_info().rss
    peak_python = peak / (1024 * 1024)
    rss_delta = max(0, rss_after - rss_before) / (1024 * 1024)
    return Measure(name, wall, max(peak_python, rss_delta), _digest(result))


def run_scale(tmpdir: str, rows: int, cols: int, label: str) -> dict:
    """Generate one scale, run both backends on the FULL contract, report."""
    f1 = os.path.join(tmpdir, f"fc_f1_{rows}_{cols}.txt")
    f2 = os.path.join(tmpdir, f"fc_f2_{rows}_{cols}.txt")
    exp = generate_pair(f1, f2, rows=rows, cols=cols)
    mb = exp["file1_mb"]

    print("=" * 78)
    print(
        f"FULL-CONTRACT SCALE: {label} | file1={mb:.1f}MB file2={exp['file2_mb']:.1f}MB"
    )
    print("=" * 78)

    native = _time(
        "native",
        lambda: NativeComparisonBackend().compare(f1, f2, KEYS, detailed=True),
    )
    duck = _time(
        "duckdb",
        lambda: DuckDBComparisonBackend().compare(f1, f2, KEYS, detailed=True),
    )

    parity_ok = native.digest == duck.digest
    for m in (native, duck):
        print(
            f"  {m.name:8s} wall={m.wall:9.3f}s  peak~{m.peak_mb:8.1f}MB  "
            f"diffs={m.digest['diff_rows_materialized']} "
            f"only1={m.digest['only_in_file1']} only2={m.digest['only_in_file2']} "
            f"stats={'Y' if m.digest['field_statistics_built'] else 'N'}"
        )
    speedup = native.wall / duck.wall if duck.wall else float("inf")
    mem_ratio = native.peak_mb / duck.peak_mb if duck.peak_mb else float("inf")
    print(
        f"  -> DuckDB FULL-CONTRACT speedup: {speedup:.1f}x vs native(pandas)  "
        f"| peak-mem reduction: {mem_ratio:.1f}x  "
        f"| contract-parity={'OK' if parity_ok else 'MISMATCH!!'}"
    )
    if not parity_ok:
        print("     native :", native.digest)
        print("     duckdb :", duck.digest)
    print()

    os.unlink(f1)
    os.unlink(f2)
    return {
        "label": label, "rows": rows, "cols": cols, "mb": round(mb, 1),
        "native_wall": round(native.wall, 3), "duck_wall": round(duck.wall, 3),
        "speedup": round(speedup, 1),
        "native_peak_mb": round(native.peak_mb, 1),
        "duck_peak_mb": round(duck.peak_mb, 1),
        "mem_reduction": round(mem_ratio, 1),
        "parity_ok": parity_ok,
    }


def main() -> None:
    """Run the full-contract benchmark across a few scales and print a summary."""
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--quick", action="store_true",
        help="run smaller scales (skip the 1M-row cases) for a fast smoke run",
    )
    args = ap.parse_args()

    tmpdir = tempfile.mkdtemp(prefix="duckfc_")
    print(f"workdir: {tmpdir}\n")

    if args.quick:
        scales = [
            (50_000, 10, "50k x narrow(10)"),
            (50_000, 100, "50k x wide(100)"),
            (200_000, 100, "200k x wide(100)"),
        ]
    else:
        scales = [
            (100_000, 10, "100k x narrow(10)"),
            (100_000, 100, "100k x wide(100)"),
            (1_000_000, 10, "1M x narrow(10)"),
            (1_000_000, 100, "1M x wide(100) [the decisive case]"),
        ]

    out = []
    for rows, cols, label in scales:
        out.append(run_scale(tmpdir, rows, cols, label))

    if not os.listdir(tmpdir):
        os.rmdir(tmpdir)

    print("MACHINE_SUMMARY_JSON_START")
    import json
    print(json.dumps(out))
    print("MACHINE_SUMMARY_JSON_END")


if __name__ == "__main__":
    main()
