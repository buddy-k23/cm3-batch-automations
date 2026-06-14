# ADR 0010: TruthSource Backend Abstraction for L2b Reconciliation

- Status: Accepted
- Date: 2026-06-03
- Accepted: 2026-06-11 (landed on trunk feature/valdo-engine-v3, commits cbc4cda, d090e79)
- Supersedes: none
- Related: `docs/ARCHITECTURE_REVIEW_2026-06-03.md` (recommendation R-01,
  probe P3), [ADR 0008](0008-extract-multi-record-reader-primitive.md),
  `scripts/e2e_lib/run_source.py`, `scripts/e2e_lib/db_truth_comparator.py`

## Context

The L2b SQL-truth gate reconciles a Valdo output file against an expected
rowset derived from a database. The comparator engine
(`scripts/e2e_lib/db_truth_comparator.py::reconcile`) is already backend-neutral
in the narrow sense that it accepts a **PEP-249 connection** and performs no
commits/rollbacks. However, the *opening* of that connection is hard-wired to
Oracle in the orchestrator: `scripts/e2e_lib/run_source.py::_open_l2b_connection`
imports `oracledb` and calls `oracledb.connect(...)` directly, and the expected
queries are Oracle-dialect SQL.

The 2026-06-03 architecture review (R-01, the top recommendation) identified
this as the load-bearing gap for the "configurable, reusable ETL-testing
application" promise: probe P3 (point the gate at a non-Oracle truth source)
is the weakest scenario precisely because there is no seam.

This ADR covers the **first slice (R-01a)** only: introduce the interface and
the Oracle adapter, with **no caller rewired and no behaviour change**.

- R-01a (this slice): add `TruthSource` interface + `OracleTruthSource`.
- R-01b: rewire `_open_l2b_connection` to use `OracleTruthSource`.
- R-01c: decouple the SQL dialect (spike + `dialect` hint consumption).

## Decision

Add `src/database/truth_source.py` exposing:

- `TruthSource` — an abstract base class with `connect() -> <PEP-249 conn>`,
  `close()`, a `dialect` property, and context-manager sugar.
- `SqlDialect` — a frozen dataclass hint (today only `name`); the placeholder
  seam for R-01c so dialect concerns can be expressed declaratively later
  without per-backend branches in the engine.
- `OracleTruthSource` — the first adapter. It mirrors the *exact* credential
  convention of the legacy opener (`ORACLE_DSN` / `ORACLE_USER` /
  `ORACLE_PASSWORD` via a secret accessor), imports `oracledb` lazily, accepts
  an injected `connect_fn` for tests, and raises a single `TruthSourceError`
  (never leaking secret values) on failure.

This is a **core `src/` change**, not E2E-harness work, so AGENTS.md hard
rule #1 does not apply; it follows the normal core MR + ADR flow (cf. ADR 0009).
It lives in `src/database/` alongside the existing connection/extractor modules.

### Dependency direction

The adapter must not import `scripts/`. The secret accessor is consumed via a
structural `_SecretLookup` protocol (`get(name) -> str`) that
`scripts.e2e_lib.secret_resolver.SecretResolver` already satisfies, keeping the
`src/` → `scripts/` direction clean.

### Rejected alternatives

- **A. Leave the Oracle call inline and add a backend flag later.** Rejected:
  defers the seam and keeps `oracledb` imported in the orchestrator.
- **B. A full repository interface returning normalized expected rowsets.**
  Rejected for this slice: larger surface, and the engine already consumes a
  PEP-249 connection cleanly. Revisit only if a non-SQL backend (Parquet)
  needs it.
- **C. Put the adapter in `scripts/e2e_lib/`.** Rejected: it is a reusable
  database primitive that belongs with `src/database/`; harness code should
  depend on it, not own it.

## Consequences

### Positive
- A stable seam so Oracle is one implementation; unblocks R-01b/R-01c and P3.
- No behaviour change and no caller touched in this slice — zero risk to the
  green L2b gate.
- Testable without the `oracledb` driver or a live DB (injected `connect_fn`).

### Negative / accepted
- A second connection path momentarily co-exists with `_open_l2b_connection`
  until R-01b rewires it. Accepted: the slices are intentionally small.
- `SqlDialect` is a near-empty placeholder until R-01c gives it consumers.

### Migration
- None. No config, schema, or data change. No existing test modified.

## Implementation order
1. **This slice (R-01a):** `src/database/truth_source.py` +
   `tests/unit/test_truth_source.py` + this ADR. *(done in this MR)*
2. R-01b: rewire `_open_l2b_connection` → `OracleTruthSource`.
3. R-01c: dialect-coupling inventory + `dialect` consumption + a skipped
   second-backend extension test.

## R-01c — dialect-coupling inventory + seam (slice 3)

R-01c is a **spike + minimal seam**, not a second backend. It (a) inventories
the Oracle-dialect coupling in the L2b SQL, and (b) threads an optional
`SqlDialect` through `reconcile()` as the consumption point, defaulting to the
historical Oracle assumption (no behaviour change).

### Where Oracle dialect is assumed today

The comparator engine itself (`db_truth_comparator.py`) is dialect-neutral: it
reads `expected_*.sql` text and runs it through a PEP-249 cursor, comparing
trimmed strings. **All dialect coupling lives in the checked-in SQL**, not the
Python:

| Coupling | Where | Notes |
|---|---|---|
| Quoted identifiers `AS "LN-NUM-ERT"` | `20_query/expected_*.sql` | Aliases the key column to the file's dash form. Oracle uses `"..."`; another backend may differ. |
| `TRIM`, `LPAD`, `TO_CHAR` | `20_query/expected_*.sql` | Format-alignment to the fixed-width file representation. ANSI-ish but not universal. |
| CTAS materialization (no `CREATE VIEW`) | `00_bootstrap/030_expected_tables.sql`, `10_load/020_refresh_expected.sql` | app_int lacks `CREATE VIEW` (session-7 shape B); helpers are CTAS tables refreshed by TRUNCATE+INSERT. |
| `q'[ ... ]'` alternative quoting | `00_bootstrap/*.sql` | Oracle-specific string quoting around `EXECUTE IMMEDIATE` payloads. |
| `ORA-00955` / PL/SQL `EXCEPTION` blocks | `00_bootstrap/*.sql` | Idempotent "create if not exists" via Oracle error trapping. |
| `app_int.` schema qualifier | bootstrap + query SQL | Source-level `staging_schema` override; backend-specific namespacing. |
| `oracledb` driver | `OracleTruthSource` (R-01a/b) | The connection opener — already behind the seam. |

### The seam (consumption point)

`reconcile(..., dialect: Optional[SqlDialect] = None)` resolves
`effective_dialect = dialect or ORACLE_DIALECT`. Today nothing branches on it
(Oracle is the only backend), but it is the single, documented place a future
backend's dialect flows into the engine. A second backend would:

1. Implement a `TruthSource` adapter (its own `connect()` + `SqlDialect`).
2. Ship its own `query_dir` with backend-dialect `expected_*.sql` (and a
   bootstrap/load strategy appropriate to it — e.g. real views where allowed).
3. Pass its `dialect` to `reconcile()`; dialect-specific handling (identifier
   quoting, materialization strategy) is then added at the seam rather than
   scattered.

This keeps the "configure, don't fork" promise pointed at probe P3 without
speculatively building a backend that has no consumer yet.

## Open questions deferred
- The eventual shape of `SqlDialect` (identifier quoting, predicate syntax,
  CTAS vs view materialization) — decided against a **concrete** second backend
  when one exists, not speculatively. R-01c only establishes the seam + the
  inventory above.
