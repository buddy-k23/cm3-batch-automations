# ADR 0024: DuckDB as a Targeted Accelerator — Scoped Assessment for Fixed-Width Validation, DB↔File Comparison, and Excel→DB Comparison

- Status: **Proposed (DRAFT)**
- Date: 2026-06-24
- Supersedes: none
- Related: prior decision **rejecting a wholesale DuckDB engine swap** (replacing
  pandas as the core engine); [ADR 0010](0010-truthsource-backend-abstraction.md)
  (backend seam pattern), [ADR 0022](0022-adapter-agnostic-db-integration.md)
  (adapter factory `DB_ADAPTER`), `src/comparators/chunked_comparator.py`,
  `src/services/db_file_compare_service.py`, `src/parsers/enhanced_validator.py`

## Context

A prior architectural decision **rejected** replacing pandas with DuckDB as the
core validation/processing engine. This ADR does **not** revisit that. It answers
a deliberately narrower question raised by the product owner: is DuckDB worth
adopting as a **targeted, optional accelerator/backend** for three specific use
cases, leaving pandas as the default engine?

1. **Fixed-width file validation** (`enhanced_validator.py`, `field_validator.py`,
   cross-field/cross-row/multi-record validators, `fixed_width_parser.py`).
2. **DB↔file comparison + validation** (`db_file_compare_service.py` →
   `extractor.py` → `compare_service.py` → `file_comparator.py` /
   `chunked_comparator.py`).
3. **Excel↔DB comparison** (no such data path exists today — see below).

The assessment is grounded in the actual code, not DuckDB's marketing.

### What the code actually does today

**Comparison engine.** `compare_service.run_compare_service` auto-routes on file
size: `should_use_chunked()` flips to the chunked path at
`CHUNK_THRESHOLD_BYTES = 50 MB`. Below that, `FileComparator` does a pandas
`merge` (inner for diffs, left+indicator for only-in sets) and then a
**Python-level `iterrows()`** loop to build field-level diffs. Above 50 MB,
`ChunkedFileComparator` streams both files into a **temporary SQLite DB**, builds
a composite index on the key columns, and computes results with a **bounded,
set-based** plan: one `INNER JOIN` for field diffs (streamed via `fetchmany`) and
two `EXCEPT` queries plus `COUNT(*)` for the only-in sets. This is already an
out-of-core, set-based reconciliation — it is *not* a per-row round-trip engine.

**DB↔file.** `compare_db_to_file` builds an adapter via `get_database_adapter()`
(`DB_ADAPTER` = oracle/postgresql/sqlite, ADR 0022), extracts the DB side to a
DataFrame (`DataExtractor.extract_by_query` / `extract_table`), writes it to a
temp pipe-delimited file (`_df_to_temp_file`), then calls the same
`run_compare_service`. The DB is **already pulled through `oracledb`/`psycopg2`/
`sqlite3` into pandas** before any comparison happens.

**Fixed-width validation.** `enhanced_validator.validate` does file-existence and
size checks, a **row-length scan in pure Python streaming** over the file
(`_validate_fixed_width_row_lengths`, `_detect_first_misalignment_by_row` —
byte-offset `substr` per field, per row), then parses to a DataFrame and runs
mostly **vectorized pandas predicates** (`field_validator.py`:
`isin`, `str.match`, `to_numeric`, `str.len`) plus the cross-row/cross-field/
multi-record engines. The recent perf work fixed pandas date-coercion and
per-row hotspots; `_is_date_candidate` gates the expensive `to_datetime`.

**Excel.** Every `read_excel`/`openpyxl`/`load_workbook` hit in `src/` is
**spec/template ingestion** — rules templates (`ba_rules_template_converter.py`,
`rules_template_converter.py`), mapping templates (`template_converter.py`,
`convert_mappings_command.py`), suite configs (`suite_template_converter.py`),
and onboarding workbooks (`onboarding/workbook_reader.py`, `workbook_schema.py`).
**There is no Excel-as-data comparison path anywhere.** Excel→DB data
reconciliation is a genuine capability gap, not an existing pandas path.

## Per-use-case assessment

### 1. Fixed-width file validation — **NOT WORTH IT**

- **FIT (poor).** DuckDB has no native fixed-width reader. The file would still
  have to be pre-parsed via `substr`/byte-offset extraction (exactly what
  `fixed_width_parser` and the misalignment scanner already do) before SQL could
  touch it. The value-add of validation here is the **rule engine** —
  `valid_values`, regex, COBOL picture-clause format checks
  (`_is_value_valid_for_format`: `S9(n)`, `9(n)V9(m)`, `X(n)`, `CCYYMMDD`),
  cross-field, cross-row (`unique`, `group_count`, `group_sum`), and
  multi-record header/detail/trailer logic. Much of that is **not naturally SQL**
  (regex picture clauses, three-state JSON/XML nullity, type-sequence checks),
  and the parts that are (group aggregates) are already cheap. The row-length and
  first-misalignment scans are inherently **per-byte, per-row** work that a
  columnar engine does not accelerate.
- **SEAM.** There is no clean single seam; DuckDB would have to replace the
  vectorized predicates in `field_validator.py` *and* re-implement the
  picture-clause/cross-record logic — i.e. a partial re-write of the rule engine,
  which is precisely the rejected "engine swap" wearing a smaller hat.
- **PAYOFF.** Near zero. The dominant historical cost (date coercion, per-row
  loops) was already addressed in pandas. DuckDB would duplicate working,
  tested code (3,850 unit tests pin this behaviour) for no measurable win, and
  introduce a second execution model the rule engine would have to straddle.
- **Verdict: not-worth-it.**

### 2. DB↔file comparison + validation — **PILOT-FIRST (narrow, conditional)**

- **FIT (partial).** The honest constraint: **the Oracle extract is unavoidable
  and unchanged.** DuckDB has no native Oracle scanner; data still comes out via
  `oracledb` into a DataFrame exactly as `DataExtractor` does today. DuckDB can
  `ATTACH` Postgres and SQLite and read Arrow/pandas zero-copy, so the *only*
  place it could win is the **compare/join step** — not extraction. But that step
  is **already set-based and out-of-core** for large files via
  `ChunkedFileComparator` (SQLite JOIN + EXCEPT). DuckDB's columnar vectorized
  JOIN/EXCEPT would likely beat SQLite on **wide files, many value columns, and
  tens-of-millions of rows**, and it avoids the current
  `_df_to_temp_file` → re-read round-trip (it can join the extract DataFrame
  against the file in-process). But for the common case (< 50 MB, in-memory
  pandas `merge`) there is no benefit, and for the existing chunked case the win
  is **incremental, not categorical** — SQLite already does the right algorithm.
- **SEAM (clean).** This is the one place a tidy seam exists: an **optional
  `ComparisonBackend`** selected behind `compare_service.run_compare_service`
  (and mirrored in `ChunkedFileComparator`), chosen by size/flag/`COMPARE_BACKEND`
  env var, pandas remaining the default. For DB↔file specifically, a DuckDB path
  in `compare_db_to_file` could push the join into DuckDB and **skip the temp-file
  serialization** by registering the extract DataFrame and reading the actual
  file with DuckDB's CSV reader. The output-contract (the result dict consumed by
  the HTML/JSON renderers) must be reproduced **byte-for-byte** — that contract,
  not the algorithm, is the integration risk.
- **EFFORT: M.** Stories: (a) `ComparisonBackend` interface + extract the SQLite
  plan behind it (refactor, no behaviour change); (b) DuckDB backend implementing
  the same JOIN/EXCEPT/field-diff contract; (c) selection logic + flag/env +
  size threshold; (d) contract-parity test matrix against the existing 50 MB
  chunked path; (e) optional temp-file-skip in `compare_db_to_file`. **Risk:**
  output-shape drift (the renderers and 3,850 tests pin the schema), a new
  runtime dependency, and a second comparison code path to maintain.
- **PAYOFF.** Wins only at the **large + wide** end (out-of-core JOIN beyond RAM,
  many columns) and by removing the extract→temp-file→re-read hop. Does **not**
  win on the Oracle extract, and does **not** beat the existing chunked path
  enough to justify itself for typical files.
- **Verdict: pilot-first** — build the seam, benchmark DuckDB vs the existing
  SQLite chunked path on a realistic wide/large SHAW-scale extract, adopt only if
  the measured win is material.

### 3. Excel↔DB comparison — **ADOPT-TARGETED (highest value-for-effort)**

- **FIT (strong).** This is the only use case where **no working path exists** to
  duplicate. DuckDB reads Excel directly (its `excel`/`st_read` extension, or via
  a thin pandas `read_excel` → `register`), and can `ATTACH` Postgres/SQLite or
  join in-process against a DataFrame extracted from any `DB_ADAPTER` backend
  (Oracle still via `oracledb`, same caveat as #2 but the **extract already
  exists** in `extractor.py`). The reconciliation is then a single columnar
  JOIN/EXCEPT — exactly DuckDB's strength — producing the **same result-dict
  contract** the comparison renderers already consume.
- **SEAM (clean, additive).** A new `excel_db_compare_service` parallel to
  `db_file_compare_service`: read Excel → (DuckDB or pandas) join against
  `DataExtractor` output → reuse `run_compare_service`'s result shape and the
  existing `HTMLReporter`. No existing path is replaced; this is **net-new
  capability**, so the regression surface is minimal and the 3,850 tests are not
  at risk.
- **EFFORT: S–M.** Stories: (a) Excel data reader (sheet/header selection — the
  ingestion converters already establish the openpyxl/`read_excel` patterns to
  reuse); (b) the compare service wiring Excel ↔ `DataExtractor` through the
  existing comparator/result contract; (c) CLI command + API router + (optionally)
  a UI tab mirroring DB Compare; (d) tests. DuckDB here is an **implementation
  detail of the join**, not a new top-level engine — and even a pandas-`merge`
  first cut would deliver the capability, with DuckDB as a drop-in accelerator for
  large workbooks later.
- **PAYOFF.** Highest, because the baseline is **zero**. Even modest Excel→DB
  reconciliation is new user value; DuckDB makes the large-workbook case scale.
- **Verdict: adopt-targeted** (capability first; DuckDB as the join engine where
  it pays).

## Ranked recommendation (value-for-effort)

| Rank | Use case | Verdict | Seam | Effort | Why |
|---|---|---|---|---|---|
| **1** | **Excel↔DB compare** | **adopt-targeted** | new `excel_db_compare_service`, reuse `run_compare_service` result contract + `HTMLReporter` | **S–M** | Only gap with **no existing path** to duplicate; net-new value; minimal regression surface |
| **2** | **DB↔file compare** | **pilot-first** | optional `ComparisonBackend` behind `compare_service` / `chunked_comparator`, flag/size-selected | **M** | Clean seam, but must **beat the existing SQLite set-based chunked path** to justify itself; benchmark before adopting |
| **3** | **Fixed-width validation** | **not-worth-it** | none clean — would re-implement the rule engine | **L (and rejected)** | No native FW reader; rich non-SQL rule engine; pandas hotspots already fixed; would duplicate 3,850 tests of working code |

### Direct answers to the three pointed questions

- **(i) Does DuckDB beat the existing `chunked_comparator` set-based path enough
  to matter?** Not categorically. The chunked path **already** does the correct
  algorithm (indexed JOIN + EXCEPT, out-of-core, bounded memory) — it just uses
  SQLite. DuckDB's columnar vectorization should win on **wide, very large**
  inputs and by avoiding the temp-file round-trip, but the gain is incremental.
  This is why DB↔file is *pilot-first, benchmark-gated*, not auto-adopt.
- **(ii) Oracle-not-native caveat.** Real and load-bearing. DuckDB cannot read
  Oracle natively; extraction stays on `oracledb` (as `extractor.py` already
  does). DuckDB only ever sees the already-extracted rows, so for Oracle the win
  is confined to the join step, never the extract.
- **(iii) Excel→DB as highest value/lowest risk.** Confirmed in code: Excel is
  **spec-ingestion only** today; there is no Excel data-comparison path. That
  makes it the highest-value, lowest-regression entry — additive, not a
  replacement of tested code.

## Decision (proposed)

1. **Reject** DuckDB for fixed-width validation.
2. **Pilot** an optional `ComparisonBackend` for DB↔file, adopting DuckDB **only**
   if a benchmark against the current SQLite chunked path on wide/large data
   shows a material win. Pandas remains the default.
   - **BENCHMARK OUTCOME (2026-06-30) — RATIFIED: ADOPT.** The pilot
     (`scripts/benchmark_duckdb_compare.py`, full results in
     `docs/duckdb_compare_benchmark.md`) shows DuckDB beats the SQLite chunked
     path **categorically, not incrementally** — at the decisive 1M×100-col
     (860 MB/side) scale: **109× vs SQLite chunked, 59× vs pandas**, at **383 MB
     peak vs pandas' 13.9 GB** (genuinely out-of-core). Correctness parity
     confirmed at every scale; DuckDB was fastest at *every* size, so there is no
     speed crossover (the only reason not to route small files is to avoid making
     `duckdb` mandatory). **Honest discount:** the harness computed *counts*, not
     the full materialized `differences`/`only_in_*`/`field_statistics` payload —
     part of DuckDB's edge is the Python `fetchmany` + per-field loop it skipped,
     so the realized win on the *full contract* is conservatively **~10–30×**, to
     be re-confirmed by a parity matrix before merge. Oracle caveat holds: DuckDB
     accelerates only the join, never the `oracledb` extract. `duckdb` stays an
     **optional** extra (15.5 MB wheel, no transitive deps), never a base
     dependency. Effort to build the backend: **M**.
3. **Adopt-targeted** an Excel↔DB comparison capability (a new service parallel to
   `db_file_compare_service`), delivering the capability with pandas first and
   DuckDB as the large-workbook join accelerator. This is the recommended **first
   move**.

In all cases pandas stays the default engine; DuckDB is an **optional, opt-in
backend for the comparison/join step only** — consistent with, and not a reversal
of, the prior decision to reject a wholesale engine swap.

## Consequences

- **Positive.** Net-new Excel→DB capability; a clean optional backend seam that
  contains DuckDB to the join step; no disruption to the validated rule engine.
- **Negative / accepted.** A new optional dependency (`duckdb`) where adopted; a
  second comparison code path to keep contract-parity with; benchmark work before
  the DB↔file pilot can be ratified.
- **Migration.** None until a slice is approved. No config/schema/data change in
  this assessment.

## Open questions deferred

- Exact Excel reader ergonomics (multi-sheet, header row, type coercion to match
  DB column types) — decided with the Excel↔DB slice.
- Whether the DuckDB DB↔file pilot also subsumes the in-memory (< 50 MB) path or
  only the chunked path — decided by the benchmark.
```
