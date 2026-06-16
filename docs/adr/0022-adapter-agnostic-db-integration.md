# ADR 0022 — Adapter-agnostic DB integration

- Status: Accepted
- Date: 2026-06-16
- Sprint: 12 (S12-1, [#402](https://github.com/buddy-k23/valdo/issues/402))
- Decision: **Route the DB-integration features (`db-compare`, `extract`, `reconcile`/`reconcile-all`, the `run-tests` Oracle gate) through the existing `get_database_adapter()` factory; extend the `DatabaseAdapter` ABC with one new method (`get_column_metadata`) and a portable canonical type model so the same mapping reconciles against Oracle, PostgreSQL, and SQLite. Keep `OracleConnection` / `transaction.py` Oracle-only internals; migrate `query_executor`, `reconciliation`, `extractor`, and `db_file_compare_service` onto the adapter seam.**
- Related:
  [ADR 0010](0010-truthsource-backend-abstraction.md) (truth-source backend abstraction — the seam this ADR generalises to the DB-integration features),
  [ADR 0020](0020-db-to-db-disposition.md) (minimal-dependency posture; the `src/database/` package inventory),
  [ADR 0021](0021-mcp-background-jobs.md) (cross-dialect-for-free via the shared engine/adapter design),
  `src/database/adapters/{base,factory,oracle_adapter,postgresql_adapter,sqlite_adapter}.py`,
  `src/database/{connection,query_executor,reconciliation,extractor,transaction,truth_source}.py`,
  `src/services/db_file_compare_service.py`,
  `src/commands/{db_compare,run_tests_command}.py`,
  `src/database/{engine,db_url}.py`, `src/config/db_config.py`,
  `docs/sprints/SPRINT_12_KICKOFF.md`.

## Context

Sprint 11 stood up a full Postgres stack — the FastAPI app, the Web UI, the MCP
server, the run-registry/run-history/baseline persistence — all running on
PostgreSQL via the shared SQLAlchemy engine (`src/database/engine.py`) and the
adapter-aware URL builder (`src/database/db_url.py`). That stack is genuinely
cross-dialect because every consumer goes through `get_engine()` /
`get_schema_prefix()` (`engine.py:11-22`, `:52-68`), which read `DB_ADAPTER` and
emit dialect-correct URLs and schema prefixes (`db_url.py:38-68`, `:95-122`).

But the **DB-integration *features*** — `valdo db-compare`, `valdo extract`,
`valdo reconcile` / `reconcile-all`, and the Oracle gate inside
`valdo run-tests` — were **not** migrated. They still go down a second,
Oracle-only code path that bypasses both the SQLAlchemy engine and the adapter
factory:

- `valdo reconcile` / `reconcile-all` construct `OracleConnection.from_env()`
  directly and hand it to `SchemaReconciler` (`src/main.py:358-372`,
  `:428-436`).
- `valdo extract` constructs `OracleConnection.from_env()` and hands it to
  `DataExtractor` (`src/main.py:626-630`).
- `valdo db-compare` → `run_db_compare_command` → `compare_db_to_file`, which
  builds an `OracleConnection` and a `DataExtractor`
  (`db_file_compare_service.py:181-190`).
- `valdo run-tests`' Oracle gate builds `OracleConnection.from_env()` +
  `DataExtractor` (`run_tests_command.py:127-141`).

Each of these depends on modules whose SQL and types are **Oracle-specific**,
so they raise or silently mis-reconcile on Postgres/SQLite. This is the
load-bearing gap: the app runs on Postgres, but the features a BA actually uses
to validate an onboarded source against the target schema do not.

The good news is that the seam already exists. The `DatabaseAdapter` ABC
(`base.py:17-161`) and three concrete adapters already abstract `connect`,
`disconnect`, `execute_query`, `get_table_columns`, `table_exists`,
`extract_to_file`, and the context-manager protocol, with the dialect logic
**already written per backend**:

| Concern | Oracle | PostgreSQL | SQLite |
|---|---|---|---|
| List columns | `ALL_TAB_COLUMNS … ORDER BY COLUMN_ID` (`oracle_adapter.py:160-165`) | `information_schema.columns … ORDER BY ordinal_position` (`postgresql_adapter.py:196-200`) | `PRAGMA table_info(...)` (`sqlite_adapter.py:143`) |
| Table exists | `ALL_TABLES` (`oracle_adapter.py:188-192`) | `information_schema.tables` (`postgresql_adapter.py:222-225`) | `sqlite_master WHERE type='table'` (`sqlite_adapter.py:161-164`) |
| Identifier case | upper (`:155`) | lower (`:205`) | as-is (PRAGMA) |
| Extract to file | chunked `fetchmany(10_000)` (`:218-244`) | server-side cursor, chunked (`:257-281`) | `fetchall` (`:192-209`) |

So the work is not "build an abstraction" — it is "point the four consumers at
the abstraction that already exists, and close the two gaps the ABC does not yet
cover (rich column metadata, and a dialect-neutral type model for
reconciliation)."

## Current state — the two DB code paths

### Portable path (Sprint 11, works on all three backends)

`engine.py` (`get_engine` is `lru_cache`'d, `pool_pre_ping=True`), `db_url.py`
(`get_db_url` branches on `DB_ADAPTER` → `oracle+oracledb` / `postgresql+psycopg2`
/ `sqlite`; `get_valdo_schema` returns `APP_INT` / `public` / `""`), `run_history`,
the run-registry, baselines, Alembic (`alembic/env.py` + `db_url.include_object`).
`db_config.py` already carries `db_adapter`, `db_host`, `db_port`, `db_name`
(`db_config.py:71-115`). **Adapter factory** `get_database_adapter()` resolves
`DB_ADAPTER` (default `oracle`) and dynamically imports the concrete adapter
(`factory.py:34-73`).

### Oracle-hard-wired path (the four features in scope)

| Module | Oracle coupling (file:line) |
|---|---|
| `connection.py` | `import oracledb`; `OracleConnection` is the only connection type; `from_env()` reads `ORACLE_*` (`connection.py:8`, `:46-70`, `:90-106`). |
| `query_executor.py` | `import oracledb`; `fetch_table_columns` queries `user_tab_columns … ORDER BY column_id` (`query_executor.py:3`, `:65-72`). |
| `reconciliation.py` | `import oracledb`; raw Oracle catalog SQL — `all_tables`/`user_tables` (`:174-186`), `all_tab_columns`/`user_tab_columns` for columns, lengths, precision/scale, nullable, NOT-NULL set (`:195-289`), `all_constraints`/`all_cons_columns` for PK/UNIQUE (`:299-326`); the `_types_compatible` matrix is Oracle type names only — `VARCHAR2/NVARCHAR2/CLOB/NCLOB/NUMBER/DATE/TIMESTAMP …` and `'boolean': [...]  # Oracle doesn't have native boolean` (`:434-441`). Nullable is the Oracle `'Y'/'N'` convention (`:101-105`, `:264-289`). |
| `extractor.py` | `import oracledb`; `extract_table` builds `… AND ROWNUM <= {limit}` (Oracle-only paging, `:48`); `extract_to_file` opens a raw `self.connection.cursor()` and traps `oracledb.Error` (`:118-146`); `get_table_stats` reads `user_segments` (`:174-182`). |
| `transaction.py` | `import oracledb`; `create_log_table` emits `VARCHAR2(...)`, `NUMBER GENERATED ALWAYS AS IDENTITY`, `SYSTIMESTAMP`, and traps `ORA-00955` (`transaction.py:309-336`). Savepoint SQL is ANSI-ish but the type DDL is Oracle-only. |
| `truth_source.py` | The ADR-0010 seam: `TruthSource` ABC + `OracleTruthSource`; lazy `oracledb` import; `SqlDialect` hint (today only `name`). Not yet consumed by the four features. |
| `db_file_compare_service.py` | Non-Oracle branch **still** falls back to `OracleConnection.from_env()`: `if connection_override and _adapter == "oracle": OracleConnection(...) else: OracleConnection.from_env()` (`db_file_compare_service.py:181-190`). The docstring even admits "Non-Oracle adapters fall back to `OracleConnection.from_env()`" (`:150`). |

**Latent signature bug found while reading:** `run_tests_command.py:141` calls
`extractor.extract_to_file(query, str(temp_file), params=oracle_params)` —
positional `query`, positional `output_file`, and a `params=` kwarg. But
`DataExtractor.extract_to_file`'s signature is
`(table_name=None, output_file=None, …, query=None)` with **no `params`
parameter** (`extractor.py:78-83`). This call is broken today regardless of
backend; the migration must reconcile the signature (see §4).

## Decision

### 1. Abstraction sufficiency — extend the ABC with one method

The current ABC covers `db-compare` and `extract` as-is (`execute_query`,
`extract_to_file`, `table_exists`, `get_table_columns`). It is **insufficient
for `reconcile`**, which needs per-column *type, nullability, length,
precision, scale* — `get_table_columns` returns only names
(`base.py:85-99`). Rather than have `reconciliation.py` issue its own catalog
SQL (which is exactly the Oracle coupling we are removing), add **one** abstract
method to `DatabaseAdapter`:

```python
def get_column_metadata(
    self, table: str, schema: Optional[str] = None
) -> dict[str, ColumnMeta]: ...
```

returning, per column name, a frozen dataclass:

```python
@dataclass(frozen=True)
class ColumnMeta:
    name: str
    canonical_type: CanonicalType     # portable enum — see §3
    raw_type: str                     # backend-native type string, kept for messages
    nullable: bool                    # True/False, normalised (no 'Y'/'N')
    length: Optional[int]             # char length; None if N/A
    precision: Optional[int]          # numeric precision; None if N/A
    scale: Optional[int]              # numeric scale; None if N/A
```

Each adapter implements `get_column_metadata` over the catalog it **already**
queries for `get_table_columns`: Oracle reads `data_type/data_length/
data_precision/data_scale/nullable` from `ALL_TAB_COLUMNS` (the exact columns
`reconciliation._get_column_details` reads today, `:217-229`); Postgres reads
`data_type/character_maximum_length/numeric_precision/numeric_scale/is_nullable`
from `information_schema.columns`; SQLite parses `PRAGMA table_info` (`type`,
`notnull`). Nullability is normalised to `bool` in the adapter, killing the
Oracle `'Y'/'N'` leak. We **reject** also adding `get_constraints` to the ABC in
this story (PK/UNIQUE reconciliation, `reconciliation.py:299-338`) — see §6.

**Picked** (extend ABC with `get_column_metadata`) over **Rejected** (keep
`get_table_columns` only; let `reconciliation.py` issue catalog SQL through
`adapter.execute_query`): the latter just relocates the Oracle SQL into the
consumer and re-creates the coupling per dialect — the opposite of routing
through the seam. One typed method keeps all dialect SQL inside the adapters
where it already lives.

### 2. Catalog / metadata reads — route off raw Oracle SQL

- `query_executor.fetch_table_columns` (`user_tab_columns`) → deleted; callers
  use `adapter.get_table_columns`.
- `reconciliation.SchemaReconciler` is reworked to hold an **adapter**, not an
  `OracleConnection`. `_table_exists` → `adapter.table_exists`;
  `_get_table_columns` → `adapter.get_table_columns`; `_get_column_details` +
  `_get_required_columns` → derived from `adapter.get_column_metadata` (required
  = `not nullable`). No `all_tables`/`user_tables`/`all_tab_columns`/
  `user_tab_columns` SQL remains in the consumer. The `owner`/`schema` argument
  threads through unchanged (adapters already accept an optional `schema`).
- `DataExtractor` and `QueryExecutor` are reworked to hold an **adapter** and
  delegate `execute_query` / `extract_to_file` to it.

The `SchemaReconciler.__init__` and `DataExtractor.__init__` signatures change
from `(connection: OracleConnection)` to `(adapter: DatabaseAdapter)`. Call
sites (`main.py`, the service) construct the adapter via
`get_database_adapter()` instead of `OracleConnection.from_env()`.

### 3. Type mapping — a portable canonical type model (the hard call)

Reconciliation today compares a mapping's declared `data_type`
(`string`/`number`/`decimal`/`integer`/`date`/`boolean`,
`mapping_parser.py:15`) against the **Oracle** type string via a hard-coded
matrix listing only Oracle type names (`reconciliation.py:434-441`). The same
mapping run against Postgres (`varchar`/`integer`/`numeric`/`boolean`/`text`)
or SQLite (`TEXT`/`INTEGER`/`REAL`/`BLOB`, dynamic typing) mis-reports every
column as a type mismatch.

**Decision: introduce a backend-neutral `CanonicalType` enum and push
*normalisation* into each adapter.** The reconciliation engine then compares
*mapping-type → canonical-type*, never *mapping-type → raw backend string*. The
matrix becomes dialect-free and lives once.

`CanonicalType` (new `src/database/adapters/types.py`):
`STRING, INTEGER, DECIMAL, FLOAT, BOOLEAN, DATE, TIMESTAMP, BINARY, UNKNOWN`.

Per-dialect normalisation table (each adapter maps its raw catalog type →
`CanonicalType`):

| `CanonicalType` | Oracle raw | PostgreSQL raw | SQLite raw (affinity) |
|---|---|---|---|
| STRING | VARCHAR2, NVARCHAR2, CHAR, NCHAR, CLOB, NCLOB | varchar, character varying, char, text, bpchar | TEXT, CHAR*, CLOB |
| INTEGER | NUMBER(p,0), INTEGER | integer, bigint, smallint, int2/4/8 | INTEGER, INT* |
| DECIMAL | NUMBER(p,s>0), NUMBER (unspec.) | numeric, decimal | NUMERIC, DECIMAL |
| FLOAT | FLOAT, BINARY_FLOAT, BINARY_DOUBLE | real, double precision, float8 | REAL, FLOAT, DOUBLE |
| BOOLEAN | (none — see below) | boolean, bool | (none — affinity NUMERIC/INTEGER) |
| DATE | DATE | date | (DATE via affinity → DATE) |
| TIMESTAMP | TIMESTAMP, TIMESTAMP WITH [LOCAL] TIME ZONE | timestamp, timestamptz | (DATETIME/TIMESTAMP via affinity) |
| BINARY | BLOB, RAW, LONG RAW | bytea | BLOB |

The mapping→canonical compatibility matrix (replaces `_types_compatible`,
dialect-free):

| mapping `data_type` | compatible `CanonicalType`(s) |
|---|---|
| string | STRING |
| integer | INTEGER |
| number | INTEGER, DECIMAL, FLOAT |
| decimal | DECIMAL, FLOAT |
| date | DATE, TIMESTAMP |
| boolean | BOOLEAN, INTEGER, STRING |

**The boolean problem, concretely.** Oracle has no native boolean
(`reconciliation.py:440`), SQLite has none (booleans live as `0/1` INTEGER or a
`CHAR` flag), Postgres does. So a mapping field declared `boolean` must reconcile
**clean** against:
- Postgres `boolean` → canonical BOOLEAN → compatible (exact).
- Oracle `NUMBER(1)` / `CHAR(1)` → canonical INTEGER / STRING → compatible
  (boolean accepts INTEGER and STRING), emitted as an **informational warning**
  ("declared boolean stored as numeric/char flag — no native boolean on this
  backend"), not an error.
- SQLite `INTEGER` → canonical INTEGER → same informational warning.

This is the key design property: **one mapping, three backends, correct
reconciliation**, with the "no native boolean" nuance demoted from an
Oracle-only hard-coded string list to a dialect-neutral, advisory rule.

SQLite's dynamic typing is handled by mapping declared-type **affinity** (the
rules SQLite itself uses: a declared type containing `INT` → INTEGER affinity,
`CHAR`/`CLOB`/`TEXT` → STRING, `REAL`/`FLOA`/`DOUB` → FLOAT, `BLOB`/empty →
BINARY/UNKNOWN). Columns with no declared type resolve to `UNKNOWN`, which is
compatible with everything and emits an informational note (SQLite cannot
assert the type) — the honest answer for a typeless backend, not a false
failure.

Length/precision/scale checks (`reconciliation.py:107-125`, `:347-395`) stay,
now reading from `ColumnMeta.length/precision/scale`. SQLite returns `None` for
these (PRAGMA has no length), so those sub-checks are skipped on SQLite (no
data → no warning), which is correct.

**Picked** (canonical enum + per-adapter normalisation) over two **Rejected**
alternatives: (a) *per-dialect matrices in `reconciliation.py`* — triples the
matrix, scatters dialect knowledge into the consumer, exactly the coupling we
are removing; (b) *string-normalise raw types in the engine* (lowercase + a big
alias dict in `reconciliation.py`) — same scatter, and it still can't express
"boolean is fine as a numeric flag here but native there." The enum makes the
backend's own normalisation the source of truth, consistent with ADR 0010's
`SqlDialect` hint living next to the adapter.

### 4. Extraction — onto `adapter.extract_to_file`, with gaps closed

`DataExtractor.extract_to_file` delegates to `adapter.extract_to_file(query,
output_path, delimiter)` (already abstract and implemented for all three,
`base.py:117-138`). Gaps to close:

- **Query vs whole-table.** The adapter takes a query string; the extractor's
  table/columns/where/limit builder stays in `DataExtractor` (portable string
  building) and the **limit clause must stop being Oracle `ROWNUM`**
  (`extractor.py:48`). Replace with the canonical `LIMIT n` for Postgres/SQLite
  and `FETCH FIRST n ROWS ONLY` for Oracle, selected via the adapter's dialect
  (a small `adapter.limit_clause(n)` helper, or branch on `DB_ADAPTER`). Bare
  `extract_table`/`extract_sample` build the SQL; `extract_to_file` passes the
  final SQL to the adapter.
- **`params` signature bug.** Fix `run_tests_command.py:141` to the real
  signature (`output_file=…, query=…`) — `params` is not supported by
  `extract_to_file` and the Oracle gate query is parameterless in practice; if
  bind params are needed they go through `execute_query`, not `extract_to_file`.
- **Fixed-width output / chunking.** The adapters already chunk
  (`fetchmany(10_000)` Oracle/PG; SQLite fetches all — acceptable for the
  reconcile/extract row volumes). Fixed-width output is **not** an adapter
  concern — `extract_to_file` emits delimited text; fixed-width rendering, where
  needed, stays in the reporting/parsing layer. No gap to close here.
- `get_table_stats`' `user_segments` query (`extractor.py:174-182`) is
  Oracle-only and not on any in-scope feature path → left Oracle-only and
  clearly labelled (raises a friendly "table stats not supported on this
  backend" on non-Oracle), or skipped. Not in the critical path.

### 5. `db_file_compare_service.py` — fix the OracleConnection fallback

Replace the `connection.py` import and the `OracleConnection(...)` /
`OracleConnection.from_env()` branch (`db_file_compare_service.py:22`,
`:181-190`) with `get_database_adapter()`. `connection_override["db_adapter"]`
selects the adapter type; the override's host/user/password are passed to the
adapter constructor (each adapter already accepts explicit constructor args:
`OracleAdapter(username,password,dsn)`, `PostgreSQLAdapter(host,port,…)`,
`SQLiteAdapter(db_path)`). `DataExtractor(adapter)` then works on any backend.
The docstring line admitting the Oracle fallback (`:150`) is corrected.

### 6. Disposition of `connection.py`, `truth_source.py`, `transaction.py`

| Module | Disposition | Rationale |
|---|---|---|
| `connection.py` (`OracleConnection`) | **Keep as Oracle internals; deprecate as a public consumer entry point.** It is functionally a subset of `OracleAdapter` (same `ORACLE_*` env, same `oracledb` connect). The four features stop importing it; it stays for backward-compat (tests patch `cx_Oracle`, `connection.py:14`) and may be used internally by `OracleAdapter` or left standalone. Not deleted in this story (avoids churn in unrelated tests). A docstring note marks it superseded by `OracleAdapter`. |
| `truth_source.py` (ADR 0010) | **Keep, unchanged; reconcile conceptually.** It is the L2b/E2E-harness seam (returns a raw PEP-249 connection for `scripts/e2e_lib`), a *different* consumer from the `DatabaseAdapter` (returns a pandas/file API for `src/` features). This ADR does **not** merge them — ADR 0010 deliberately scoped `TruthSource` to "produce a connection" for the comparator engine. We note the overlap and leave a follow-up to consider unifying `SqlDialect`/`CanonicalType` once a second `TruthSource` backend actually lands (ADR 0010's own deferral). No change here. |
| `transaction.py` | **Out of scope; stays Oracle-only, clearly labelled.** `create_log_table`'s `VARCHAR2`/`NUMBER … IDENTITY`/`SYSTIMESTAMP` DDL (`:309-336`) is demo/test table creation and a `TransactionLogger` not on any of the four feature paths (`db-compare`/`extract`/`reconcile`/`run-tests` never instantiate it). Migrating savepoint/transaction management to all three dialects is real work with **no in-scope consumer** — explicitly deferred. Add a module docstring line: "Oracle-only; not used by the adapter-routed DB-integration features." |

### 7. Test strategy — prove portability on SQLite (and the PG full-stack)

The proof is **SQLite-first** because it needs no server and runs in the
existing `tests/unit/` gate (the same reason `engine.py`/run-registry chose it,
per ADR 0021).

- **Unit — adapter metadata parity** (`tests/unit/test_adapter_column_metadata.py`):
  for each adapter, create a table with a known column set and assert
  `get_column_metadata` returns the right `ColumnMeta` (canonical type,
  nullable bool, length/precision/scale). SQLite runs live (in-memory); Oracle
  and Postgres adapters tested with a fake PEP-249 connection / monkeypatched
  `pd.read_sql` returning canned catalog rows (the adapters already support
  injected modules, e.g. `PostgreSQLAdapter._get_psycopg2`).
- **Unit — type-mapping matrix** (`tests/unit/test_canonical_types.py`): table
  driving every (mapping `data_type` × `CanonicalType`) pair through the new
  compatibility matrix, plus the three boolean cases (PG native, Oracle
  NUMBER/CHAR, SQLite INTEGER) asserting "compatible, informational warning, not
  error".
- **Integration — reconcile on SQLite** (`tests/unit/test_reconcile_sqlite.py`):
  `DB_ADAPTER=sqlite`, `DB_PATH=:memory:`; `CREATE TABLE` a target with mixed
  types incl. a boolean-as-INTEGER and a NOT NULL column; reconcile a real
  mapping JSON fixture; assert zero spurious type-mismatch errors, the expected
  nullable/unmapped warnings, and `valid=True`. **This is the headline AC** — the
  same mapping that reconciles against Oracle now reconciles against SQLite.
- **Integration — extract + db-compare on SQLite**
  (`tests/unit/test_extract_db_compare_sqlite.py`): seed a SQLite table, run
  `DataExtractor.extract_to_file` (assert pipe-delimited output + row count),
  then `compare_db_to_file` against that file (assert `status="passed"`).
  Exercises the §5 fallback fix end-to-end with `connection_override=
  {"db_adapter":"sqlite",...}`.
- **Fixtures:** one shared `sqlite_target_table` fixture (in-memory connection +
  DDL + seed rows) and a `recon_mapping_fixture` JSON under
  `tests/unit/fixtures/`. Reuse the existing mapping-parser fixtures.
- **Postgres full-stack (where feasible):** add `reconcile` + `extract` to the
  Sprint-11 Postgres smoke (the existing full-stack run already has a live PG)
  as a CI job that runs `valdo reconcile` and `valdo extract` against a seeded
  PG table and asserts exit 0. Guarded behind the PG-available CI condition;
  not part of the local `tests/unit/` gate.

## Implementation breakdown

S-sized stories (≤5 pts each at the house cap). LOC = net new/changed.

| # | Story | Files touched | Est. LOC |
|---|---|---:|---:|
| S12-1a | **ABC + canonical types.** Add `ColumnMeta` + `get_column_metadata` to `base.py`; new `adapters/types.py` (`CanonicalType` + mapping→canonical matrix + per-dialect normalisers); implement `get_column_metadata` in all three adapters. Unit: metadata parity + type matrix. | `adapters/base.py`, `adapters/types.py` (new), `adapters/{oracle,postgresql,sqlite}_adapter.py`, `tests/unit/test_adapter_column_metadata.py`, `tests/unit/test_canonical_types.py` | ~340 |
| S12-1b | **Reconciliation onto the adapter.** Rework `SchemaReconciler` + `MappingValidator` to hold a `DatabaseAdapter`; replace catalog SQL with `get_column_metadata`/`table_exists`/`get_table_columns`; replace `_types_compatible` with canonical matrix; normalise nullable to bool. Rewire `main.py reconcile`/`reconcile-all` to `get_database_adapter()`. Integration: reconcile on SQLite. | `database/reconciliation.py`, `database/query_executor.py`, `src/main.py` (reconcile + reconcile-all), `tests/unit/test_reconcile_sqlite.py`, existing reconcile tests | ~300 |
| S12-1c | **Extract + db-compare onto the adapter.** Rework `DataExtractor`/`QueryExecutor` to delegate to the adapter; fix the `ROWNUM`→dialect-`LIMIT` paging; fix the `run_tests_command.py:141` signature bug; replace the `OracleConnection` fallback in `db_file_compare_service.py` with `get_database_adapter()`. Rewire `main.py extract` + `run_tests` gate. Integration: extract + db-compare on SQLite. | `database/extractor.py`, `database/query_executor.py`, `services/db_file_compare_service.py`, `commands/run_tests_command.py`, `src/main.py` (extract), `tests/unit/test_extract_db_compare_sqlite.py` | ~280 |
| S12-1d | **Disposition labels + docs + PG smoke.** Deprecation docstrings on `connection.py`/`transaction.py`; non-Oracle guards on `extractor.get_table_stats`/`transaction` paths; PG full-stack CI job for reconcile/extract; `docs/USAGE_AND_OPERATIONS_GUIDE.md` + `DOCUMENTATION_INDEX.md` + Sphinx `modules.rst` for `adapters/types.py`. | `database/{connection,transaction,extractor}.py` (docstrings/guards), `.github/workflows/*` (PG smoke), `docs/*`, `docs/sphinx/modules.rst` | ~140 |

**Total: ~1,060 LOC across four S-sized stories.**

### One sprint or two — explicit judgment

**This must span two sprints.** ~1,060 LOC of *core DB-path* change — touching
the reconciliation type engine (the genuinely hard, correctness-sensitive
piece), the extractor, a security-sensitive service, two CLI commands, and three
adapters — exceeds what a single 5-pt sprint absorbs at the house cadence (the
comparable ADR-0021 worker and ADR-0020 absorb were both scoped as
ADR-plus-skeleton-then-fast-follow for less correctness risk). Crucially, S12-1a
+ S12-1b are the **load-bearing, must-get-right** half (the canonical type model
and the reconciliation rewrite — where a wrong matrix silently passes a bad
mapping); rushing them alongside the extract/service rewrite invites exactly the
mis-reconciliation this ADR exists to prevent.

**Proposed split:**

- **Sprint 12 (this story, #402): S12-1a + S12-1b.** Ship the ABC extension, the
  canonical type model, and the reconciliation migration with the SQLite
  integration proof. `reconcile`/`reconcile-all` become adapter-agnostic — the
  hardest call (type mapping) lands and is proven on SQLite. `extract`/
  `db-compare` remain on the Oracle path for one more sprint (they *work* on
  Oracle today; they are not regressed). ~640 LOC, fits a 5-pt sprint with the
  correctness focus the type model demands.
- **Sprint 13 follow-up: S12-1c + S12-1d.** Extract + db-compare migration, the
  signature-bug fix, the service fallback fix, disposition labelling, and the PG
  full-stack smoke. ~420 LOC. Lands the remaining two features and the
  cross-backend CI proof on the foundation S12-1a/b established.

This split is also the natural seam: S12-1a/b have **no dependency** on the
extract/service work, and S12-1c reuses the `get_column_metadata`/adapter
plumbing S12-1a ships — so Sprint 13 starts from a green, proven base rather
than a half-migrated one.

## Consequences

### Positive

- **All four DB-integration features run on Oracle, PostgreSQL, and SQLite** via
  the one factory the Sprint-11 stack already uses — closing the gap between
  "app runs on Postgres" and "the validation features run on Postgres."
- **The same mapping reconciles correctly against any backend.** The canonical
  type model demotes Oracle-specific type strings (and the "no native boolean"
  nuance) from a hard-coded matrix to a dialect-neutral, advisory rule.
- **Dialect SQL stays inside the adapters** (where it already lives and is
  already tested), not scattered into consumers — the coupling is removed, not
  relocated.
- **SQLite-first tests run in the existing unit gate** with no server, giving
  real portability proof on every PR (consistent with ADR 0021's rationale).
- **A real latent bug is fixed** (`run_tests_command.py:141` `params=` call
  against an incompatible signature).
- **Zero new runtime dependencies** — uses the adapters, factory, and pandas
  already present (ADR 0020 posture preserved).

### Negative / risks

- **Risk: canonical-type matrix gets a mapping wrong** (false pass or false
  fail). Mitigation: the exhaustive type-matrix unit test (S12-1a) and the
  SQLite reconcile integration test (S12-1b) are ACs; the matrix is dialect-free
  and lives in one file, so a fix is one-place.
- **Risk: SQLite's typelessness yields `UNKNOWN` over-permissiveness.**
  Accepted and made honest — `UNKNOWN` is informational, not a silent pass; the
  test asserts the note is emitted. SQLite is a dev/CI backend, not a prod
  reconciliation target.
- **Risk: two-sprint split leaves `extract`/`db-compare` Oracle-only for a
  sprint.** Accepted: they are not regressed (Oracle works today); the split is
  along a clean dependency seam and the high-risk half lands first.
- **Risk: `SchemaReconciler`/`DataExtractor` constructor signature change
  (`OracleConnection` → `DatabaseAdapter`) breaks external/test callers.**
  Mitigation: in-repo callers are all in scope (`main.py`, the service,
  `run_tests_command`); existing reconcile/extract unit tests are updated in the
  same story; `OracleConnection` is kept (not deleted) so any out-of-tree caller
  still imports.
- **Risk: PG full-stack smoke is flaky / unavailable in some CI contexts.**
  Mitigation: guarded behind the PG-available condition; the SQLite tests are
  the authoritative portability gate, PG smoke is corroborating.

### Follow-ups (not filed by this story)

- **S12-1c + S12-1d as the Sprint 13 follow-up** (extract/db-compare migration +
  disposition + PG smoke), per the split above.
- **Unify `SqlDialect` (ADR 0010) with `CanonicalType`** if/when a second
  `TruthSource` backend lands — deferred exactly as ADR 0010 deferred its own
  dialect shape; do not build speculatively.
- **PK/UNIQUE constraint reconciliation** (`reconciliation.py:299-338`) cross-
  dialect: add `get_constraints` to the ABC. Currently Oracle-only; the
  constraint check degrades to "skipped" on non-Oracle until then (no false
  failure). File when a non-Oracle source needs key-constraint reconciliation.
- **`transaction.py` cross-dialect DDL** — file only if a feature path needs the
  `TransactionLogger`/log-table on a non-Oracle backend.
