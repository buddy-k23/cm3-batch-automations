# The two DB-to-file compare engines: division of labour

> Architecture-review recommendation **R-09** (`docs/ARCHITECTURE_REVIEW_2026-06-03.md`,
> dimension 3). Cross-linked from [`docs/architecture.md`](architecture.md).

Valdo has **two** code paths that compare database-derived data against a Valdo
output (or batch) file. They look superficially alike — both end with "does the
file match the database?" — but they answer **different questions, from
different truth sources, for different consumers**. They are **complementary by
design, not redundant**. This document records the division of labour so the two
do not silently drift into one another.

## At a glance

| | `compare_db_to_file` | `db_truth_comparator.reconcile` |
|---|---|---|
| **Module** | `src/services/db_file_compare_service.py` | `scripts/e2e_lib/db_truth_comparator.py` |
| **Layer** | Production core (`src/`) | E2E harness (`scripts/e2e_lib/`) |
| **Pipeline / gate** | `db_compare` pipeline step; `valdo db-compare` CLI; `POST` DB-compare API | L2b SQL-truth gate (`run_source.py::_run_l2b_sql_truth`) |
| **Truth source** | A live Oracle **query or table** the caller names | Per-record-type **`expected_*.sql`** views over the same Oracle staging data the Java code reads |
| **Inputs** | `query_or_table`, parsed mapping dict, an actual file, optional key columns / transforms / connection override | A loaded `ReconciliationSpec` (umbrella YAML + per-type `expected_*.sql`), a PEP-249 `conn`, the output file |
| **Comparison model** | Whole-file structural + row-level compare via `run_compare_service` (extract → temp pipe-delimited file → file-vs-file) | Per-record-type, key-indexed field compare with cardinality checks and cross-type assertions; collects **every** violation |
| **Record awareness** | Flat / single logical record shape (mapping `fields`) | Multi-record umbrella aware (composes `multi_record_file_parser` / ADR 0008) |
| **Output** | `{workflow, compare}` dict with a pass/fail status | `ReconciliationReport` (frozen): per-type counts + sorted `ReconciliationViolation`s |
| **Connection lifecycle** | Owns it (`OracleConnection.from_env` or override) | Caller-supplied; engine never commits/rolls back |
| **Primary consumer** | Ad-hoc operators, the UI DB-compare tab, pipeline `db_compare` steps | The automated E2E batch gate (L2b) |

## `compare_db_to_file` — the ad-hoc / pipeline DB-compare engine

**Purpose.** Answer "does this batch file match what Oracle currently holds for
this query/table?" on demand, with minimal ceremony. It is the engine behind the
`db_compare` pipeline step, the `valdo db-compare` CLI command, and the UI's
DB-compare tab.

**Truth source.** Whatever the caller points it at: a bare table name
(`SHAW_SRC_P327`, `APP_INT.FOO`) or an arbitrary `SELECT`. The caller chooses the
slice of the database to treat as truth.

**How it works.** Extract the rows (`DataExtractor`), optionally apply
mapping-defined field transforms, write them to a temporary pipe-delimited file,
then delegate to the shared `run_compare_service` for the actual structural and
row-level diff. The result is the same `compare` payload every other Valdo
file-vs-file comparison produces, wrapped with a small `workflow` summary
(`status`, `db_rows_extracted`, `query_or_table`).

**When to use it.**

- An operator wants a quick, parameterised "DB vs file" check without authoring a
  reconciliation spec.
- A pipeline needs a `db_compare` step against a named table or query.
- The comparison is **flat** (one logical record shape) and the truth is "this
  query's rows", not "the full per-record-type contract of a multi-record file".

## `db_truth_comparator.reconcile` — the L2b SQL-truth gate engine

**Purpose.** Be the deterministic, multi-record-aware **gate** that reconciles a
Valdo output file row-by-row against a SQL truth source derived from the same
Oracle staging data the upstream Java code reads from (issue #17). It is the
engine driving the L2b gate in the E2E batch harness.

**Truth source.** Per-record-type `expected_*.sql` views, declared in a checked-in
`ReconciliationSpec` (umbrella YAML + SQL files). The spec — not Python — decides
which record types are covered, the key columns, the cardinality, and any
cross-type assertions (e.g. trailer count equals the sum of detail rows). Adding a
new source or file type is "check in a YAML spec plus `expected_*.sql`; no Python
edits required".

**How it works.** Bootstrap/load the SQL, execute each record type's expected
query and index it by the spec's key tuple, iterate the file once via the shared
multi-record reader primitive (ADR 0008), then compare per key. It emits typed
violations — `field_mismatch`, `missing_expected`, `unexpected_file_row`,
`cardinality_violation`, `assertion_failed`, `unknown_record_type` — collecting
**all** of them (no fail-fast) in a deterministic sort order, because the gate's
reporting layer needs the full picture.

**When to use it.**

- The automated E2E batch gate (L2b) needs a reproducible, spec-driven verdict.
- The file is **multi-record** (umbrella with discriminated record types) and the
  contract includes per-type keys, cardinality, and cross-type assertions.
- The truth is a curated, version-controlled SQL contract, not an ad-hoc query.

## Why two, not one

They sit at different points on two axes:

1. **Breadth of contract.** `compare_db_to_file` is a *whole-result* diff against
   one query/table. `reconcile` enforces a *per-record-type contract* (keys,
   cardinality, assertions) across a multi-record file.
2. **Audience and lifecycle.** `compare_db_to_file` is a production-core,
   on-demand operator/pipeline tool with an owned connection and a generic
   compare payload. `reconcile` is a harness gate driven by a declarative,
   checked-in spec, fed a caller-managed connection, returning a frozen report
   tuned for gate reporting.

Both verdicts are individually correct for their truth source; this mirrors the
deliberate two-gate complementarity recorded in
[`docs/adr/0013-empty-header-only-batch-gate-semantics.md`](adr/0013-empty-header-only-batch-gate-semantics.md)
("keep L1 and L2b complementary — different truth sources — not redundant").

## Is convergence intended?

**No active convergence is planned, and the two are expected to remain distinct
for the foreseeable future.** They serve different audiences (ad-hoc/pipeline vs
the automated gate), different contract breadths (flat query diff vs per-type
spec), and live in different layers (`src/` production core vs `scripts/e2e_lib/`
harness) under AGENTS.md hard rule #1 (no `src/` changes for harness work).

The one place a future overlap *could* arise is the underlying comparison
primitives: if a non-Oracle truth source, a delimited output, or an
order-sensitive sequence rule is ever needed, the natural move is to share more
primitives **beneath** both engines (e.g. via the `TruthSource` abstraction,
ADR 0010 / R-01x) rather than to merge the two public entry points. Until such a
driver exists, merging them would conflate two correct-but-different questions.

**Open question for the team:** should the `db_compare` pipeline step ever be
allowed to consume a `ReconciliationSpec` (gaining per-type/cardinality/assertion
semantics in the pipeline), or should spec-driven reconciliation stay
gate-only? This is intentionally left **undecided** here; it would warrant its
own ADR if and when a concrete need arises.
