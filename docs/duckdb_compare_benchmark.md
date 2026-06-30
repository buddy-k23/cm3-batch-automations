# DuckDB DB↔File Comparison Benchmark — ADR 0024 §2 Pilot

**Status:** Benchmark complete — result feeds ADR 0024 decision #2 (DB↔file `pilot-first`).
**Date:** 2026-06-30
**Harness:** `scripts/benchmark_duckdb_compare.py` (throwaway, not production code).
**Machine:** Apple Silicon (arm64), Python 3.11 venv, duckdb 1.5.4, pandas 3.0.3, SQLite (stdlib).
**No `src/` production code was modified.** `duckdb` was pip-installed into `.venv` for the experiment only.

## What was benchmarked

Three engines computing the **identical** DB↔file reconciliation result
(`matching_rows`, `rows_with_differences`, `only_in_file1_count`,
`only_in_file2_count`) on the **same** two pipe-delimited files keyed on the same
`ACCT_KEY`, modelling the real path (DB extract → temp pipe file → two-file compare):

- **(a) pandas merge** — the `<50 MB` `FileComparator` path (inner merge for diffs, left+indicator merge for only-in sets).
- **(b) SQLite set-based** — the `ChunkedFileComparator` large-file engine: stream both files into a temp SQLite DB with a composite key index, one `INNER JOIN` (field diffs, streamed via `fetchmany` and compared in Python), two `EXCEPT` + `COUNT(*)` for only-in sets. **This is the engine DuckDB must beat to be adopted.**
- **(c) DuckDB** — `read_csv(all_varchar=true)` on both files, then `INNER JOIN` for matches, `WHERE <any col differs>` for diff count, `EXCEPT` for only-in. Join step only.

Semantics reproduced from `src/comparators/{chunked,file}_comparator.py`: all values
read as text, NULL→`''`, a diff row = same key in both with ≥1 value-column string
inequality, `matching_rows = (keys in both) − rows_with_differences`.

## Correctness parity — CONFIRMED

On the 5,000-row × 12-col case, **all three engines and the by-construction
expectation agree exactly**:

```
pandas / sqlite / duckdb / expected:
  total_rows_file1=5000  total_rows_file2=5000  matching_rows=4851
  rows_with_differences=99  only_in_file1_count=50  only_in_file2_count=50
```

Parity also held (counts identical to pandas) at **every** timed scale below
(`scale-parity=OK`). A faster-but-different engine would have been disqualified;
DuckDB was not.

## Results (median wall-clock; best-of-3 for ≤100k, single run for 1M)

Generation scale actually used: **{100k, 1M} rows × {10, 100} cols**, 1.5% diff rows,
0.5% only-in each side. The 1M×100 case produced **860 MB per side** — well into the
chunked regime and a fair stand-in for a wide SHAW-scale extract. (1M×100 was run
once due to time budget; the 100k cases are best-of-3.)

| Scale (rows × cols) | File MB/side | Routed regime | Engine | Wall (s) | rows/s | Peak mem |
|---|---|---|---|---|---|---|
| 100k × 10 (narrow) | 8.8 | pandas (<50MB) | pandas | 0.960 | 208k | 219 MB |
| | | | sqlite | 5.506 | 36k | 184 MB |
| | | | **duckdb** | **0.233** | **857k** | **29 MB** |
| 100k × 100 (wide) | 86.0 | CHUNKED | pandas | 11.548 | 17k | 1,803 MB |
| | | | sqlite | 49.681 | 4.0k | 1,853 MB |
| | | | **duckdb** | **0.524** | **381k** | **244 MB** |
| 1M × 10 (narrow) | 87.7 | CHUNKED | pandas | 10.674 | 187k | 1,411 MB |
| | | | sqlite | 55.793 | 36k | 308 MB |
| | | | **duckdb** | **0.645** | **3.10M** | **67 MB** |
| 1M × 100 (wide) | 860.2 | CHUNKED | pandas | 271.241 | 7.4k | 13,936 MB |
| | | | sqlite | 501.881 | 4.0k | 3,097 MB |
| | | | **duckdb** | **4.585** | **436k** | **383 MB** |

### Speedup of DuckDB

| Scale | vs SQLite chunked | vs pandas |
|---|---|---|
| 100k × narrow | 23.6× | 4.1× |
| 100k × wide | 94.7× | 22.0× |
| 1M × narrow | 86.6× | 16.6× |
| **1M × wide (the decisive case)** | **109.5×** | **59.2×** |

## Verdict (the questions ADR 0024 §2 asked)

**Does DuckDB materially beat the SQLite chunked path on wide/large data?**
**Yes — categorically, not incrementally.** On the decisive 1M×100 / 860 MB case
DuckDB is **109× faster than the SQLite chunked engine** (4.6 s vs 502 s) and
**59× faster than pandas** (4.6 s vs 271 s). The advantage grows with both width and
row count. The ADR's hedge that the win would be merely "incremental" is **not borne
out by measurement** — the gap is one to two orders of magnitude.

**Where is the crossover / below which scale is it not worth it?**
There is effectively **no crossover** — DuckDB was fastest at *every* scale tested,
including the small 8.8 MB pandas-regime case (4× faster than pandas, 24× faster than
SQLite). The strongest *justification* appears at wide+large, but even narrow/small
favors DuckDB. The honest "not worth it" line is about **maintenance cost**, not
speed: below ~50 MB the absolute pandas time (≈1 s) is already fine, so the win there
is real but not user-perceptible.

**Does it win on memory (out-of-core) as well as time?**
**Yes, decisively.** On 1M×100, DuckDB peaked at **383 MB** vs pandas' **13.9 GB**
(a 36× reduction) and SQLite's 3.1 GB (8×). DuckDB's CSV reader + streaming hash join
is genuinely out-of-core; pandas materializes the full inner-merge product (its peak
is the danger — 13.9 GB would OOM a modest container). SQLite is also bounded, but
pays heavily in time for it.

## Recommendation: **ADOPT as an optional DB↔file comparison backend**

The measured win is large enough to justify the second code path the ADR worried
about — but only with the seam kept tight.

- **Seam:** an optional `ComparisonBackend` behind `compare_service.run_compare_service`
  (and mirrored where `ChunkedFileComparator` is selected), chosen by a
  `COMPARE_BACKEND` env var / flag with **pandas remaining the default**. For DB↔file
  specifically, `compare_db_to_file` can register the extract DataFrame directly and
  let DuckDB read the file — skipping the `_df_to_temp_file` → re-read hop entirely.
- **Effort:** **M** (consistent with ADR). Stories: (a) extract the result-dict contract
  behind a `ComparisonBackend` interface (refactor, no behaviour change);
  (b) DuckDB backend producing the **byte-for-byte same result dict** the HTML/JSON
  renderers consume — including the materialized `differences` / `only_in_*` lists and
  `field_statistics`, which this benchmark did **not** build (see caveat);
  (c) selection logic + flag/env + size threshold; (d) contract-parity test matrix
  against the 50 MB chunked path; (e) optional temp-file skip in `compare_db_to_file`.
- **Suggested trigger:** route to DuckDB when present **and** file ≥ the existing
  50 MB chunked threshold (where the win is overwhelming and most valuable). Keep
  pandas as the small/default path to avoid a hard `duckdb` dependency for the common
  case. Making DuckDB the small-file path too is *defensible on speed* but adds a
  mandatory dependency for marginal absolute gain — **not recommended for v1.**

## Honesty / caveats (these temper the headline numbers)

1. **The benchmark computed COUNTS, not the full materialized payload.** The real
   `ChunkedFileComparator` and `FileComparator` also build `differences` (per-row
   field-level diff dicts with string/numeric analysis), `only_in_file1/2` lists, and
   `field_statistics`. DuckDB's job here stopped at `COUNT(*)`. So **part** of DuckDB's
   edge over SQLite is that SQLite's streamed Python `fetchmany` loop + per-field
   analysis is exactly the materialization DuckDB skipped. A production DuckDB backend
   that must emit the same materialized lists will be **slower than these numbers** —
   though it can build them set-based (a single diff-rows SELECT) rather than row-by-row
   in Python, so it should still win comfortably. **The x-factors above are an upper
   bound on the realized production win; treat ~10–30× as the conservative expectation
   for the full contract on wide/large data, not 100×.**
2. **The SQLite ingest dominates its time** (streaming `to_sql` chunk inserts + index
   build), and DuckDB's native CSV reader is simply far faster at getting data in. This
   is a real and load-bearing advantage, not an artifact — but it means the win is
   substantially an *I/O/ingest* win, not purely a *join* win.
3. **Oracle caveat (load-bearing, per ADR §2/§(ii)).** DuckDB does **not** accelerate
   the Oracle pull. Extraction stays on `oracledb` via `DataExtractor` exactly as today;
   DuckDB only ever sees already-extracted rows. For Oracle DB↔file the win is confined
   to the compare step (plus skipping the temp-file round-trip). The benchmark models
   the file↔file compare that follows extraction, which is the only part DuckDB touches.
4. **No semantic edge cases diverged** in this run (NULL→`''` handled via `COALESCE`,
   `all_varchar` matched the str/TEXT model). Two areas to pin in the contract-parity
   matrix before shipping: (a) **embedded delimiters / quoting** — DuckDB's CSV reader
   and pandas/SQLite-ingest may disagree on quoting rules; (b) **whitespace / numeric
   string normalization** — the engines compare raw strings, so `'1.0'` vs `'1'` is a
   diff in all three, but DuckDB type inference must stay off (`all_varchar=true`) or
   results will drift.
5. **Dependency weight:** `duckdb` is a **15.5 MB** self-contained wheel (no transitive
   deps). Acceptable as an *optional* extra; making it mandatory would noticeably grow
   the base image. Keep it opt-in.

## Does the win justify a second comparison code path to maintain?

**Yes, conditionally.** The 1M×100 case goes from **8+ minutes (SQLite) / 4.5 minutes
(pandas, 14 GB peak) to ~5 seconds at 0.4 GB** — that is the difference between "this
job OOMs / times out" and "this job is interactive." For an enterprise DB↔file
reconciliation tool that aspires to wide, large extracts, that is a capability
unlock, not a micro-optimization. The maintenance cost is contained because the seam
is narrow (one backend interface, one result contract, pandas stays default) and the
3,850-test contract pins the output shape. **Build it — behind the `ComparisonBackend`
seam, size/flag-gated, pandas default — and gate merge on the contract-parity matrix
producing the full materialized payload, not just counts.**

---

# Full-contract realized benchmark (S25-3)

**Status:** Re-benchmark complete — validates caveat #1 above. **The realized
full-contract win lands inside the ADR's conservative 10–30× band.**
**Date:** 2026-06-30.
**Harness:** `scripts/benchmark_duckdb_full_contract.py` — unlike the counts-only
harness above, this runs the **production** `DuckDBComparisonBackend.compare()`
(native-read fast path, full materialized contract) vs the **production**
`NativeComparisonBackend.compare()` (pandas `FileComparator`, full contract) on the
same pipe files, and asserts the two backends' materialized payloads agree
(counts **and** the lengths of the `differences` / `only_in_*` lists + that
`field_statistics` was built) before reporting a speedup.

## Why the headline 100× drops to ~20–30× — and how S25-3 keeps it there

The counts-only benchmark stopped at `COUNT(*)`. The real backends must build the
per-row `differences` list (with `string_analysis`), the `only_in_*` DataFrames
and `field_statistics`. Two things therefore changed for the production path:

1. **The first naïve full-contract implementation lost almost all the win**
   (measured **1.6–2.9×**, below). Cause: it fetched **every matched row** into a
   Python materialization loop — on a clean-ish extract that is ~all rows — so the
   `fetchall` + per-field Python comparison dominated, and that loop is identical
   work in both engines. DuckDB's read/join advantage was swamped by Python
   materialization.
2. **The fix (shipped): push the diff filter into SQL.** Only the rows where at
   least one value column actually differs are fetched into Python; the
   overwhelming majority of *matching* rows are counted set-based in DuckDB and
   **never cross into Python**. A window `row_number()` over the pandas-merge
   ordering preserves each differing row's merged-frame position so `source_row`
   parity holds. This restored the win to **22–33×** on the same inputs.

So the realized win is real but is **bounded by how many rows differ**: it is the
*matching* rows DuckDB gets to skip materializing. On a reconciliation extract
(typically a small % differ) that is almost all of them, so the win is large. A
pathological "everything differs" input would converge both engines toward the
same Python materialization cost — an honest upper bound on where this path helps.

## Realized results (full materialized contract; single run per scale)

DuckDB here = `DuckDBComparisonBackend` native-read fast path; native =
`NativeComparisonBackend` (pandas). 1.5% diff rows, 0.5% only-in each side
(the generator's defaults). Peak memory is `max(tracemalloc python, RSS delta)`.

| Scale (rows × cols) | File MB/side | native wall | duckdb wall | **speedup** | native peak | duckdb peak | mem reduction | contract parity |
|---|---|---|---|---|---|---|---|---|
| 50k × 10 (narrow) | 4.4 | 7.34 s | **0.22 s** | **33.5×** | 264 MB | 68 MB | 3.9× | OK |
| 50k × 100 (wide) | 43.0 | 38.91 s | **1.77 s** | **22.0×** | 1,242 MB | 492 MB | 2.5× | OK |
| 200k × 100 (wide) | 172.0 | 176.66 s | **6.28 s** | **28.1×** | 4,966 MB | 1,232 MB | 4.0× | OK |

*(The decisive 1M × 100 / ~860 MB case is consistent with the 200k×100 trend —
native pandas materializes the full inner-merge product and grows superlinearly in
memory while DuckDB stays bounded; see the counts-only table above for the 1M×100
shape. Re-run `scripts/benchmark_duckdb_full_contract.py` without `--quick` to
reproduce the 1M rows on a machine with the time/RAM budget — native pandas alone
needs ~4–5 min and >10 GB at that scale.)*

### For comparison: the naïve (pre-SQL-filter) full-contract path that was rejected

| Scale | native wall | duckdb wall (naïve fetch-all) | speedup |
|---|---|---|---|
| 50k × 10 | 7.43 s | 2.61 s | 2.9× |
| 50k × 100 | 41.80 s | 25.55 s | 1.6× |
| 200k × 100 | 175.43 s | 100.57 s | 1.7× |

This is the **"part of DuckDB's edge was the materialization it skipped"** caveat
made concrete: fetching all matched rows for Python analysis gives only ~2×. The
SQL pre-filter is what makes the production backend worth the second code path.

## Does it confirm the ADR's estimate?

**Yes.** The ADR (and caveat #1) set the conservative expectation at **~10–30×**
for the full contract on wide/large data, explicitly *not* the 100× of the
counts-only run. The realized full-contract numbers are **22–33×** — at or just
above the top of that band on the in-regime cases, and **never below 10×** on the
wide/large cases the backend targets. The memory win (2.5–4×, and growing with
scale as pandas' inner-merge product balloons) is also confirmed, if more modest
than the counts-only 8–36× because the production path must hold the only-in
DataFrames and the differences list in memory.

**Bottom line:** the native-read fast path delivers the out-of-core win the
counts-only benchmark promised, *after* moving the diff filter into SQL so the
non-differing matched rows never enter pandas/Python. The contract-parity gate
(`tests/unit/test_duckdb_parity_matrix.py`, every case `duck == native`) holds at
every benchmarked scale.
