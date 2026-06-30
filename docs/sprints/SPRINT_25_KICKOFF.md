# Sprint 25 — Kickoff (Optional DuckDB DB↔File Comparison Backend)

**Sprint goal:** Build the **optional DuckDB comparison backend** the ADR 0024 §2 pilot ratified — an opt-in accelerator for DB↔file (and file↔file) comparison that produces the **identical** result contract as today's engine, gated behind a **parity matrix**. Pandas/SQLite stays the default; DuckDB is selected by flag/size only when present. The benchmark showed **109× vs the SQLite chunked path / 59× vs pandas at 1M×100 cols (860 MB)** at **36× less memory** — this sprint turns that into a maintainable, parity-proven optional path.

**Duration:** 1–2 weeks · **Capacity:** ~5 points (5 × S) · **Demo:** internal — `COMPARE_BACKEND=duckdb` reconciles a large DB↔file compare with byte-identical results to the default engine, far faster.

**Context:** ADR 0024 decision #2 was "pilot-first, benchmark-gated"; the 2026-06-30 benchmark (`scripts/benchmark_duckdb_compare.py`, `docs/duckdb_compare_benchmark.md`) **ratified ADOPT**. **Critical honesty carried from the pilot:** the benchmark measured *counts*, not the full materialized `differences`/`only_in_*`/`field_statistics` payload — so this sprint's central risk is **contract parity**, and S25-3 (the parity matrix) is the merge gate, not an afterthought. `duckdb` is an **optional** dependency (15.5 MB wheel, no transitive deps), never a base requirement — mirror the redis/fakeredis optional-test-dep pattern in `requirements-dev.txt`. Oracle extraction stays on `oracledb`; DuckDB only ever accelerates the join.

---

## Stories

| ID | GitHub | Title | Size |
|---|---|---|---|
| **S25-1** | [#452](https://github.com/buddy-k23/valdo/issues/452) | ComparisonBackend seam (default = current engine, no behavior change) | S |
| **S25-2** | [#453](https://github.com/buddy-k23/valdo/issues/453) | DuckDB backend — FULL materialized contract | S |
| **S25-3** | [#454](https://github.com/buddy-k23/valdo/issues/454) | Parity matrix gate (DuckDB == existing engine) | S |
| **S25-4** | [#455](https://github.com/buddy-k23/valdo/issues/455) | Backend selection + opt-in wiring (pandas default) | S |
| **S25-5** | [#456](https://github.com/buddy-k23/valdo/issues/456) | db-compare/excel-compare integration (skip temp-file round-trip) | S |

**Total: ~5 pts.**

---

## Sequencing — sequential, commit-per-story

Seams: engine selection in `src/services/compare_service.py::run_compare_service` (the `use_chunked` branch; `CHUNK_THRESHOLD_BYTES = 50 MB`, `should_use_chunked()`); the existing engines `src/comparators/file_comparator.py` (pandas) + `src/comparators/chunked_comparator.py` (SQLite set-based); the DB→temp-file round-trip in `src/services/db_file_compare_service.py::_df_to_temp_file` (+ `excel_db_compare_service`). Result contract: the dict `run_compare_service` returns (`matching_rows`, `only_in_file1/2`, `differences`, `field_statistics`, structure fields). Optional-dep precedent: `requirements-dev.txt` redis/fakeredis (lazy import, skip-if-absent).

| Day | Story | Notes |
|---|---|---|
| 1–2 | **S25-1** Backend seam (#452) | Introduce a thin `ComparisonBackend` abstraction at the engine-selection point. Refactor the CURRENT pandas + SQLite-chunked engines behind it as the **default** backend, with ZERO behavior change (same result dict, same routing). Tests assert the default path is byte-identical to pre-refactor output (pin with a golden test). No DuckDB yet. |
| 3–4 | **S25-2** DuckDB backend (#453) | A `DuckDbComparisonBackend` computing the FULL materialized contract — matching rows, `only_in_file1/2` LISTS (not just counts), per-row `differences`, and `field_statistics` in the exact shape `FileComparator` emits. `read_csv(all_varchar=true)` both sides + JOIN/anti-join/EXCEPT for the sets, then materialize the diff rows. Lazy `import duckdb` (raise a clear "install duckdb" error only when the backend is actually selected); graceful when absent. |
| 5–6 | **S25-3** Parity matrix (#454) | THE GATE. A parametrized parity matrix asserting DuckDB == default engine on: CSV quoting / embedded delimiters / quotes, NULL vs empty-string, leading-zero & numeric-looking keys (type-inference-off), wide (100+ cols) and narrow, only-in-each on both sides, multi-column value diffs, duplicate keys, header-only/empty files. Add `duckdb` to `requirements-dev.txt` as an OPTIONAL test dep; tests `skipif` duckdb missing. Any divergence fails the gate. |
| 7 | **S25-4** Selection + wiring (#455) | Select the backend via `COMPARE_BACKEND` env/flag (values e.g. `auto`/`pandas`/`duckdb`); `auto` uses DuckDB only when importable AND file ≥ the 50 MB threshold, else the current engine. Pandas/SQLite remains the default with `duckdb` absent. Surface the choice in `run_compare_service`. Docs: USAGE guide + a one-liner in the benchmark doc. `duckdb` stays optional (NOT in base `requirements.txt`). |
| 8 | **S25-5** Service integration (#456) | When the DuckDB backend is active, skip `_df_to_temp_file` in `db_file_compare_service` (and `excel_db_compare_service`) by registering the extracted DataFrame directly into the DuckDB connection — removing the extract→temp-file→re-read hop the benchmark flagged. Both services produce identical results either backend. Tests prove parity + that the temp-file write is skipped. |
| — | Buffer / review | Kickoff final commit; push; close #452–456. Optional DuckDB backend live, parity-proven, pandas still default. |

---

## Definition of Done

- [ ] **S25-1:** `ComparisonBackend` seam with the current engines as the default backend; golden test proves the default path output is unchanged
- [ ] **S25-2:** DuckDB backend emits the FULL result contract (only-in LISTS + per-row differences + field_statistics), not counts; lazy optional import
- [ ] **S25-3:** Parity matrix passes — DuckDB == default engine across the edge-case matrix; `duckdb` an optional `requirements-dev.txt` dep; tests skip cleanly when absent
- [ ] **S25-4:** `COMPARE_BACKEND` selection (`auto`/`pandas`/`duckdb`); pandas default; DuckDB only when present AND ≥50 MB under `auto`; `duckdb` NOT in base requirements
- [ ] **S25-5:** db-compare + excel-compare skip the temp-file round-trip under DuckDB with identical results; test asserts parity + skip
- [ ] Both CI gates **0 failed** (unit coverage ≥80%, integration); the default (no-duckdb) suite stays green with duckdb uninstalled; no regression to compare/db-compare/excel-compare/validation
- [ ] Each story = one conventional commit `(S25-<m>, #<issue>)`; #452–456 closed
- [ ] Kickoff lands as the final commit; push

---

## Risks

| Risk | Mitigation |
|---|---|
| **Contract parity** — DuckDB result diverges from the materialized payload (the pilot only checked counts) | S25-3 parity matrix is the merge GATE; cover quoting/NULL/type-inference/wide/only-in/dupes explicitly; any diff fails |
| Making `duckdb` a hard dependency by accident | Lazy import, `auto` falls back to the current engine when absent; CI default suite runs with duckdb UNINSTALLED; only `requirements-dev.txt` carries it |
| A second comparison code path to maintain | Keep DuckDB behind the ONE backend interface; the default engine is untouched; parity matrix guards drift |
| CSV edge cases (embedded delimiters, quotes, leading zeros) parse differently in DuckDB vs the pandas/SQLite reader | Pin `all_varchar=true` + explicit delimiter/quote settings; assert against the edge-case fixtures in S25-3 |
| Oracle assumed accelerated | Explicitly NOT — extract stays on `oracledb`; DuckDB registers the already-extracted frame (S25-5) |
| Golden-output drift breaking S25-1 | Capture the golden from current code BEFORE refactor; refactor must reproduce it exactly |

## Out of scope
Fixed-width validation on DuckDB (ADR 0024 §1 — rejected); making DuckDB the default; Oracle-native DuckDB scanning; the Excel large-workbook DuckDB join (separate, later). Pandas stays the default engine everywhere.

## Roles
Dev: `senior-fullstack-fintech-dev` per story. Owner/PM: you. Parity matrix (S25-3) is the ratification gate before S25-4 wires it into selectable use.
