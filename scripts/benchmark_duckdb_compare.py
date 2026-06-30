"""THROWAWAY benchmark harness for ADR 0024 #2 (DB<->file comparison pilot).

Decides whether DuckDB should become an optional ComparisonBackend for the
DB<->file compare path. It is NOT production code and must not be imported by
src/. It compares three engines that must all compute the SAME reconciliation
result (matching / only_in_file1 / only_in_file2 / rows_with_differences) on the
SAME two pipe-delimited files keyed on the SAME key_columns:

  (a) pandas merge       -- the <50MB FileComparator path
  (b) SQLite set-based   -- the ChunkedFileComparator large-file path (the
                            current engine DuckDB must beat to be adopted)
  (c) DuckDB             -- read_csv on both files + INNER JOIN + anti-join +
                            field-diff, confined to the join step.

Semantics reproduced from src/comparators/{chunked,file}_comparator.py:
  - all values read as TEXT/str, NULL/NA -> '' (empty string)
  - a "difference" row = same key present in both, >=1 value column differs
    by string inequality
  - matching_rows = (#keys present in both) - (#rows_with_differences)
  - only_in_fileN = keys present in fileN but not the other (key-only set)

Run:  .venv/bin/python scripts/benchmark_duckdb_compare.py
"""
from __future__ import annotations

import gc
import os
import random
import sqlite3
import string
import tempfile
import time
import tracemalloc
from dataclasses import dataclass
from typing import Callable

import duckdb
import pandas as pd
import psutil

RNG = random.Random(42)
DELIM = "|"
THRESHOLD_MB = 50  # CHUNK_THRESHOLD_BYTES routing point


# --------------------------------------------------------------------------- #
# Data generation
# --------------------------------------------------------------------------- #
def _rand_str(n: int) -> str:
    return "".join(RNG.choices(string.ascii_uppercase + string.digits, k=n))


def generate_pair(
    path1: str,
    path2: str,
    rows: int,
    cols: int,
    diff_frac: float = 0.015,
    only_frac: float = 0.005,
) -> dict:
    """Write two pipe-delimited files modelling a DB extract vs a real file.

    file1 = simulated DB extract, file2 = the file. Both share the same schema:
    key column ACCT_KEY plus (cols-1) value columns. A controlled fraction of
    shared rows differ in 1-3 value columns; a controlled fraction of rows are
    only-in-file1 and only-in-file2 so every engine does real diff work.

    Returns the EXPECTED counts (computed by construction) for parity checks.
    """
    value_cols = [f"COL{i:03d}" for i in range(cols - 1)]
    header = "ACCT_KEY" + DELIM + DELIM.join(value_cols)

    n_only1 = int(rows * only_frac)
    n_only2 = int(rows * only_frac)
    n_shared = rows - n_only1
    n_diff = int(n_shared * diff_frac)

    # Pre-generate a base value row template reused with light variation so we
    # don't pay full random cost per cell for wide files (still realistic).
    def base_values() -> list[str]:
        return [_rand_str(8) for _ in value_cols]

    expected = {
        "total_rows_file1": n_shared + n_only1,
        "total_rows_file2": n_shared + n_only2,
        "matching_rows": n_shared - n_diff,
        "rows_with_differences": n_diff,
        "only_in_file1_count": n_only1,
        "only_in_file2_count": n_only2,
    }

    with open(path1, "w") as f1, open(path2, "w") as f2:
        f1.write(header + "\n")
        f2.write(header + "\n")

        # Shared rows (some differing)
        for i in range(n_shared):
            key = f"K{i:09d}"
            vals = base_values()
            line1 = key + DELIM + DELIM.join(vals)
            f1.write(line1 + "\n")
            if i < n_diff:
                # mutate 1-3 value columns in file2
                v2 = list(vals)
                for _ in range(RNG.randint(1, min(3, len(v2)))):
                    j = RNG.randrange(len(v2))
                    v2[j] = _rand_str(8)
                f2.write(key + DELIM + DELIM.join(v2) + "\n")
            else:
                f2.write(line1 + "\n")

        # only_in_file1
        for i in range(n_only1):
            key = f"A{i:09d}"
            f1.write(key + DELIM + DELIM.join(base_values()) + "\n")

        # only_in_file2
        for i in range(n_only2):
            key = f"B{i:09d}"
            f2.write(key + DELIM + DELIM.join(base_values()) + "\n")

    expected["file1_mb"] = os.path.getsize(path1) / (1024 * 1024)
    expected["file2_mb"] = os.path.getsize(path2) / (1024 * 1024)
    expected["value_cols"] = value_cols
    return expected


# --------------------------------------------------------------------------- #
# Engine (a): pandas merge  (mirrors FileComparator key-based path)
# --------------------------------------------------------------------------- #
def run_pandas(path1: str, path2: str, keys: list[str]) -> dict:
    df1 = pd.read_csv(path1, sep=DELIM, dtype=str, keep_default_na=False)
    df2 = pd.read_csv(path2, sep=DELIM, dtype=str, keep_default_na=False)

    # only-in sets via left merge + indicator on keys (FileComparator._find_unique_rows)
    m1 = df1.merge(df2[keys], on=keys, how="left", indicator=True)
    only1 = int((m1["_merge"] == "left_only").sum())
    m2 = df2.merge(df1[keys], on=keys, how="left", indicator=True)
    only2 = int((m2["_merge"] == "left_only").sum())

    # field diffs via inner merge (FileComparator._find_detailed_differences)
    merged = df1.merge(df2, on=keys, how="inner", suffixes=("_file1", "_file2"))
    value_cols = [c for c in df1.columns if c not in keys]
    diff_mask = None
    for col in value_cols:
        col_diff = merged[f"{col}_file1"] != merged[f"{col}_file2"]
        diff_mask = col_diff if diff_mask is None else (diff_mask | col_diff)
    rows_with_diff = int(diff_mask.sum()) if diff_mask is not None else 0

    return {
        "total_rows_file1": len(df1),
        "total_rows_file2": len(df2),
        "matching_rows": len(df1) - only1 - rows_with_diff,
        "rows_with_differences": rows_with_diff,
        "only_in_file1_count": only1,
        "only_in_file2_count": only2,
    }


# --------------------------------------------------------------------------- #
# Engine (b): SQLite set-based  (mirrors ChunkedFileComparator)
# --------------------------------------------------------------------------- #
def run_sqlite(path1: str, path2: str, keys: list[str], chunk_size: int = 100000) -> dict:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    db_path = tmp.name
    conn = sqlite3.connect(db_path)

    def q(ident: str) -> str:
        return '"' + ident.replace('"', '""') + '"'

    try:
        # read header to know columns
        with open(path1) as fh:
            cols = fh.readline().strip().split(DELIM)
        value_cols = [c for c in cols if c not in keys]
        keys_sql = ", ".join(q(c) for c in keys)

        for table, path in (("file1", path1), ("file2", path2)):
            col_defs = ", ".join(f"{q(c)} TEXT" for c in cols)
            conn.execute(f"CREATE TABLE {table} ({col_defs})")
            conn.execute(f"CREATE INDEX idx_{table} ON {table} ({keys_sql})")
            for chunk in pd.read_csv(
                path, sep=DELIM, dtype=str, keep_default_na=False, chunksize=chunk_size
            ):
                chunk.to_sql(table, conn, if_exists="append", index=False)
            conn.commit()

        # field diffs: single INNER JOIN, stream rows, compare in Python (exact
        # ChunkedFileComparator semantics)
        on_clause = " AND ".join(f"f1.{q(c)} = f2.{q(c)}" for c in keys)
        select_cols = [f"f1.{q(c)} AS {q(c)}" for c in keys]
        for col in value_cols:
            select_cols.append(f'f1.{q(col)} AS {q(col + "__1")}')
            select_cols.append(f'f2.{q(col)} AS {q(col + "__2")}')
        query = (
            f"SELECT {', '.join(select_cols)} FROM file1 f1 JOIN file2 f2 ON {on_clause}"
        )
        cur = conn.execute(query)
        col_names = [d[0] for d in cur.description]
        idx1 = [col_names.index(c + "__1") for c in value_cols]
        idx2 = [col_names.index(c + "__2") for c in value_cols]

        matched = 0
        rows_with_diff = 0
        while True:
            rows = cur.fetchmany(chunk_size)
            if not rows:
                break
            for row in rows:
                matched += 1
                for a, b in zip(idx1, idx2):
                    v1 = row[a] if row[a] is not None else ""
                    v2 = row[b] if row[b] is not None else ""
                    if v1 != v2:
                        rows_with_diff += 1
                        break

        def only_in(src: str, other: str) -> int:
            exc = f"SELECT {keys_sql} FROM {src} EXCEPT SELECT {keys_sql} FROM {other}"
            return conn.execute(f"SELECT COUNT(*) FROM ({exc})").fetchone()[0]

        only1 = only_in("file1", "file2")
        only2 = only_in("file2", "file1")

        n1 = conn.execute("SELECT COUNT(*) FROM file1").fetchone()[0]
        n2 = conn.execute("SELECT COUNT(*) FROM file2").fetchone()[0]

        return {
            "total_rows_file1": n1,
            "total_rows_file2": n2,
            "matching_rows": matched - rows_with_diff,
            "rows_with_differences": rows_with_diff,
            "only_in_file1_count": only1,
            "only_in_file2_count": only2,
        }
    finally:
        conn.close()
        if os.path.exists(db_path):
            os.unlink(db_path)


# --------------------------------------------------------------------------- #
# Engine (c): DuckDB  (read_csv + set-based join/anti-join/field-diff)
# --------------------------------------------------------------------------- #
def run_duckdb(path1: str, path2: str, keys: list[str]) -> dict:
    with open(path1) as fh:
        cols = fh.readline().strip().split(DELIM)
    value_cols = [c for c in cols if c not in keys]

    def qi(ident: str) -> str:
        return '"' + ident.replace('"', '""') + '"'

    con = duckdb.connect()
    try:
        # Read both files with DuckDB's native CSV reader. all_varchar=true to
        # match the str/TEXT semantics of the other two engines; nullstr left
        # default but we coalesce to '' in SQL so NULL==NULL match parity holds.
        col_spec = ", ".join("'" + c + "': 'VARCHAR'" for c in cols)

        def reader(path: str) -> str:
            return (
                "read_csv('" + path + "', delim='" + DELIM + "', header=true, "
                "all_varchar=true, auto_detect=false, "
                "columns={" + col_spec + "})"
            )

        con.execute(f"CREATE VIEW f1 AS SELECT * FROM {reader(path1)}")
        con.execute(f"CREATE VIEW f2 AS SELECT * FROM {reader(path2)}")

        n1 = con.execute("SELECT COUNT(*) FROM f1").fetchone()[0]
        n2 = con.execute("SELECT COUNT(*) FROM f2").fetchone()[0]

        on_clause = " AND ".join(f"f1.{qi(c)} = f2.{qi(c)}" for c in keys)
        # field-diff: any value column differs (COALESCE NULL->'' to match)
        diff_expr = " OR ".join(
            f"COALESCE(f1.{qi(c)}, '') <> COALESCE(f2.{qi(c)}, '')" for c in value_cols
        )
        matched = con.execute(
            f"SELECT COUNT(*) FROM f1 JOIN f2 ON {on_clause}"
        ).fetchone()[0]
        rows_with_diff = con.execute(
            f"SELECT COUNT(*) FROM f1 JOIN f2 ON {on_clause} WHERE {diff_expr}"
        ).fetchone()[0]

        key_sel = ", ".join(qi(c) for c in keys)
        only1 = con.execute(
            f"SELECT COUNT(*) FROM (SELECT {key_sel} FROM f1 "
            f"EXCEPT SELECT {key_sel} FROM f2)"
        ).fetchone()[0]
        only2 = con.execute(
            f"SELECT COUNT(*) FROM (SELECT {key_sel} FROM f2 "
            f"EXCEPT SELECT {key_sel} FROM f1)"
        ).fetchone()[0]

        return {
            "total_rows_file1": n1,
            "total_rows_file2": n2,
            "matching_rows": matched - rows_with_diff,
            "rows_with_differences": rows_with_diff,
            "only_in_file1_count": only1,
            "only_in_file2_count": only2,
        }
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# Timing / memory harness
# --------------------------------------------------------------------------- #
@dataclass
class Measure:
    name: str
    wall: float
    peak_mb: float
    result: dict


def time_engine(name: str, fn: Callable[[], dict], best_of: int = 3) -> Measure:
    walls = []
    peak_mb = 0.0
    result = None
    proc = psutil.Process()
    for i in range(best_of):
        gc.collect()
        rss_before = proc.memory_info().rss
        tracemalloc.start()
        t0 = time.perf_counter()
        result = fn()
        wall = time.perf_counter() - t0
        cur, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rss_after = proc.memory_info().rss
        # Peak python-allocated via tracemalloc; RSS delta as cross-check for
        # native (duckdb/sqlite) allocations tracemalloc misses.
        peak_python = peak / (1024 * 1024)
        rss_delta = max(0, rss_after - rss_before) / (1024 * 1024)
        peak_mb = max(peak_mb, peak_python, rss_delta)
        walls.append(wall)
    walls.sort()
    median = walls[len(walls) // 2]
    return Measure(name, median, peak_mb, result)


def fmt(x: float) -> str:
    return f"{x:,.3f}"


def parity_check(results: dict[str, dict], expected: dict) -> list[str]:
    fields = [
        "total_rows_file1", "total_rows_file2", "matching_rows",
        "rows_with_differences", "only_in_file1_count", "only_in_file2_count",
    ]
    problems = []
    ref = results["pandas"]
    for eng, r in results.items():
        for f in fields:
            if r[f] != ref[f]:
                problems.append(f"{eng}.{f}={r[f]} != pandas.{f}={ref[f]}")
    # also vs constructed expectation
    for f in fields:
        if ref[f] != expected[f]:
            problems.append(f"pandas.{f}={ref[f]} != expected.{f}={expected[f]}")
    return problems


def main():
    tmpdir = tempfile.mkdtemp(prefix="duckbench_")
    print(f"workdir: {tmpdir}\n")

    # ---- Step 1: correctness parity on a small case (all three engines) ----
    p1 = os.path.join(tmpdir, "small1.txt")
    p2 = os.path.join(tmpdir, "small2.txt")
    exp = generate_pair(p1, p2, rows=5000, cols=12, diff_frac=0.02, only_frac=0.01)
    keys = ["ACCT_KEY"]
    small = {
        "pandas": run_pandas(p1, p2, keys),
        "sqlite": run_sqlite(p1, p2, keys),
        "duckdb": run_duckdb(p1, p2, keys),
    }
    problems = parity_check(small, exp)
    print("=" * 78)
    print("CORRECTNESS PARITY (small 5k x 12, all three engines)")
    print("=" * 78)
    for eng, r in small.items():
        print(f"  {eng:8s} {r}")
    print(f"  expected {dict((k, exp[k]) for k in r)}")
    if problems:
        print("  !! PARITY FAILURES:")
        for p in problems:
            print(f"     {p}")
    else:
        print("  PARITY: OK — all three engines + construction agree.")
    print()

    # ---- Step 2: timed scales ----
    # (rows, cols, label). Threshold ~50MB. wide=~100 cols.
    scales = [
        (100_000, 10, "100k x narrow(10)"),
        (100_000, 100, "100k x wide(100)"),
        (1_000_000, 10, "1M x narrow(10)"),
        (1_000_000, 100, "1M x wide(100)"),
    ]

    rows_out = []
    for rows, cols, label in scales:
        f1 = os.path.join(tmpdir, f"f1_{rows}_{cols}.txt")
        f2 = os.path.join(tmpdir, f"f2_{rows}_{cols}.txt")
        e = generate_pair(f1, f2, rows=rows, cols=cols)
        mb = e["file1_mb"]
        regime = "CHUNKED" if mb >= THRESHOLD_MB else "pandas"
        print("=" * 78)
        print(f"SCALE: {label}  | file1={mb:.1f}MB file2={e['file2_mb']:.1f}MB "
              f"| routed regime: {regime}")
        print("=" * 78)

        engines = {
            "pandas": lambda: run_pandas(f1, f2, keys),
            "sqlite": lambda: run_sqlite(f1, f2, keys),
            "duckdb": lambda: run_duckdb(f1, f2, keys),
        }
        # best-of-3 for <=100k, best-of-1 for 1M (time budget)
        best_of = 3 if rows <= 100_000 else 1
        measures = {}
        for name, fn in engines.items():
            m = time_engine(name, fn, best_of=best_of)
            measures[name] = m

        # parity at scale (counts only)
        ref = measures["pandas"].result
        scale_parity = all(
            measures[e2].result == ref for e2 in measures
        )
        total_rows = e["total_rows_file1"] + e["total_rows_file2"]
        for name, m in measures.items():
            rps = total_rows / m.wall if m.wall else 0
            mbps = (mb + e["file2_mb"]) / m.wall if m.wall else 0
            print(f"  {name:8s} wall={fmt(m.wall):>9}s  "
                  f"rows/s={rps:>12,.0f}  MB/s={mbps:>7.1f}  "
                  f"peak~{m.peak_mb:>7.1f}MB")
            rows_out.append({
                "scale": label, "rows": rows, "cols": cols, "mb": round(mb, 1),
                "regime": regime, "engine": name, "wall": round(m.wall, 3),
                "rows_per_s": int(rps), "mb_per_s": round(mbps, 1),
                "peak_mb": round(m.peak_mb, 1),
            })
        # speedup factors vs duckdb
        d = measures["duckdb"].wall
        sp_sqlite = measures["sqlite"].wall / d if d else 0
        sp_pandas = measures["pandas"].wall / d if d else 0
        print(f"  -> DuckDB speedup: {sp_sqlite:.1f}x vs SQLite, "
              f"{sp_pandas:.1f}x vs pandas  | scale-parity={'OK' if scale_parity else 'MISMATCH'}")
        print()

        os.unlink(f1)
        os.unlink(f2)

    # cleanup small
    for p in (p1, p2):
        if os.path.exists(p):
            os.unlink(p)
    os.rmdir(tmpdir) if not os.listdir(tmpdir) else None

    # machine-readable summary for the doc
    print("MACHINE_SUMMARY_JSON_START")
    import json
    print(json.dumps(rows_out))
    print("MACHINE_SUMMARY_JSON_END")


if __name__ == "__main__":
    main()
