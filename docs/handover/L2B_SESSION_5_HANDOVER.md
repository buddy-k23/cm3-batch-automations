# L2b SQL-Truth Gate — Session 5 Handover

**Generated:** 2026-05-28
**Predecessor:** [`docs/handover/L2B_SESSION_4_HANDOVER.md`](L2B_SESSION_4_HANDOVER.md) — read first; everything in this doc is a *delta* on session 4, not a replacement.
**Branch at handover:** `feature/issue-11-kill-file-search` (trunk, head `9f24172`, pushed to origin).
**Trunk at handover:** `feature/issue-11-kill-file-search`.
**Issues this session touched:** #17 (all three commits landed, merged to trunk).

---

## 1. Where you are in the workflow

Session 5 finished issue #17 end-to-end. The branch
`feature/issue-17-l2b-sql-truth-engine` had been left at commit 1 of 3 at
the end of session 2; sessions 3 and 4 carved out and completed #21
(the reader-primitive refactor) so that commit 2 could be a thin
wrapper. Session 5 picked the #17 branch back up, rebased it onto the
post-#21 trunk, completed commits 2 and 3, and merged the whole thing
to trunk via single-developer fast-forward.

#17 is **done and on origin**. #18 is now unblocked.

| Item | State | Notes |
|---|---|---|
| #17 commit 1 of 3 | ✅ Landed (`6dc7826` post-rebase) | `ReconciliationSpec` model and YAML loader — session 2 |
| #17 commit 2 of 3 | ✅ Landed (`9ea396f`) | SQL bootstrap + multi-record file parser |
| #17 commit 3 of 3 | ✅ Landed (`9f24172`) | `db_truth_comparator` engine |
| #17 merged to trunk | ✅ Fast-forwarded, pushed to origin | `9172f5e..9f24172` |
| `feature/issue-17-l2b-sql-truth-engine` branch | 🗑 Deleted locally | Work is on trunk now |
| `stash@{0}` (session-4 baseline leftover) | 🗑 Dropped | Per session-5 user direction |
| `stash@{1}` (session-2 conftest+api WIP) | ⏸ Still present, untouched | Disposition pending per session-2 §6 |
| #18, #19, #20 | ⏸ Now unblocked by #17 merge | Per design |

## 2. The dependency chain — current state

```
#21  ✅ merged to trunk (session 4)
#17  ✅ all 3 commits landed and merged to trunk          ← session 5
#18  ⏸ SHAW TRANERT SQL artifacts — unblocked, this is next
#19  ⏸ SHAW TRANERT reconciliation YAML + wiring
#20  ⏸ tests + CI + infographic pill flip to "wired"
```

## 3. Decisions made in session 5 (do not re-litigate)

These were confirmed by the user during the session before code was
written or after specific findings surfaced:

| Decision | Choice | Why |
|---|---|---|
| `stash@{0}` disposition | **Drop** | Empty-stat session-4 baseline-capture leftover; no value to keep |
| `stash@{1}` disposition | **Leave alone** | Same status as sessions 2/3/4 — session-2 §6 still applies |
| Read `src/config/mapping_parser.py` mid-commit-2 to confirm the slicing primitive | **Yes** | Discovered the file is dataframe-oriented (no `position`/`length`); the right primitive is `src/config/universal_mapping_parser.py::UniversalMappingParser` |
| Reconciliation comparator's driver-query model | **Join-on-key against each per-type expected SQL** | Spec models `expected_sql` per record type, no separate driver query; this matches the existing surface area |
| Bind parameters in expected SQL | **None in this release** | Spec doesn't model them; future field + ADR if needed |
| Assertion grammar scope | **File-side only** | `AssertionSpec.expr` examples reference file-side anchors; SQL-side reference is out of scope until a use case appears |
| Umbrella loading in the engine | **At top of `reconcile()` via `MultiRecordConfig.model_validate`**, errors translated to `DbTruthComparatorError` | Caller catches one error type for any engine-level defect |
| Single-developer fast-forward to trunk for #17 | **Yes** (matches session-4 §5.1 pattern for #21) | User is sole developer; MR review process not in play |
| Delete the feature branch after merge | **Yes** | Trunk has the work; the local branch is now dead weight |

## 4. What changed in this session — concretely

### 4.1 Rebase

`feature/issue-17-l2b-sql-truth-engine` was at `1abdfd2` on the
pre-session-3 trunk (`dd63736`). Session 5 rebased it onto the
current trunk `9172f5e` (post-#21). The rebase was conflict-free —
the only #17 commit at that point added `scripts/e2e_lib/reconciliation_spec.py`
plus its test file, neither of which existed in the rebase range.
Commit 1's new SHA after rebase: `6dc7826` (subject unchanged).

Test sanity check post-rebase:
`pytest tests/unit/test_e2e_reconciliation_spec.py` → 36/36 pass,
**Windows exit code 0** (the conftest unicode fix from session 4
is now on trunk and reflected in the rebase range).

### 4.2 Commit 2 of 3 — `9ea396f feat: add SQL bootstrap and multi-record file parser`

**Files added (4, +1,303 LOC):**

- `scripts/e2e_lib/sql_bootstrap.py` — `run_sql_directory(conn,
  directory, *, params=None)`. Iterates sorted `*.sql` files, splits
  each into statements at `;`-on-its-own-line or `/` in column 1
  (Oracle PL/SQL terminator), executes via the PEP-249 cursor with
  optional `:param` substitution. Returns frozen
  `SqlBootstrapResult(files_run, statements_executed, files)` with
  per-file `SqlFileResult(file_name, statements_executed)`. Single
  `SqlBootstrapError(ValueError)`. Idempotency is a convention
  (CREATE OR REPLACE / MERGE / IF NOT EXISTS); the bootstrap does
  not enforce it. **Connection is not committed or rolled back** —
  policy is the caller's.

- `scripts/e2e_lib/multi_record_file_parser.py` — `iter_parsed_rows(
  file_path, umbrella_config, mapping_paths)`. Thin wrapper around
  the #21 primitive (`src.validators.multi_record_reader.read_multi_record_file`).
  For each `ParsedRow`, slices `raw_line` into a `dict[field_name,
  trimmed_value]` using `UniversalMappingParser` from
  `src/config/universal_mapping_parser.py`. For `UnknownRecordTypeRow`,
  honours `umbrella_config.default_action`: `"error"` raises,
  `"warn"`/`"skip"` yield a `ParsedFileRow` with `record_type=None`
  and empty `fields`. Mapping presence is checked **eagerly at call
  time** (fail-fast matches the primitive); JSON loading is lazy and
  cached per invocation. Extra `mapping_paths` entries that the
  umbrella does not declare are tolerated. Yields `ParsedFileRow`
  with a `MappingProxyType`-wrapped `fields` to keep frozen-row
  semantics consistent. Single `MultiRecordFileParserError(ValueError)`.

- `tests/unit/test_e2e_sql_bootstrap.py` — 21 tests across 5 classes
  (directory handling, statement splitting, ordering+binding, failure
  surfacing, connection policy, result types). PEP-249 connection
  stubbed via `MagicMock`.

- `tests/unit/test_e2e_multi_record_file_parser.py` — 16 tests across
  5 classes (happy path, unknown discriminator under all three
  `default_action` values, eager validation, mapping cache, field
  slicing edge cases). Synthetic 3-record-type fixture (HDR/DET/TRL).

**Quality gates at commit time:**
- 37/37 new tests pass, exit code 0.
- 51/51 `test_multi_record_validator.py`, 14/14 `test_multi_record_reader.py`,
  36/36 `test_e2e_reconciliation_spec.py` still pass.
- `black` clean on all 4 files.
- `mypy --follow-imports=silent` clean on both new production modules.
- Full-suite delta verified: 30 pre-existing trunk failures unchanged
  with my files in vs out of the tree.

### 4.3 Commit 3 of 3 — `9f24172 feat: add db_truth_comparator engine`

**Files added (2, +1,975 LOC):**

- `scripts/e2e_lib/db_truth_comparator.py` — `reconcile(conn, spec,
  file_path, *, skip_bootstrap=False) -> ReconciliationReport`.
  Composes the three primitives:
  1. Pre-validates every assertion expression up front.
  2. Runs `spec.bootstrap_dir` then `spec.load_dir` via
     `run_sql_directory`.
  3. Loads `spec.umbrella_mapping` into a `MultiRecordConfig`.
  4. Derives `mapping_paths` from the umbrella's per-type `mapping`
     field for `iter_parsed_rows`.
  5. Executes each record type's `expected_sql`, indexes by spec
     `key`.
  6. Iterates the file once, building a parallel file-row index.
  7. Per record type, compares expected vs file: `field_mismatch`,
     `missing_expected`, `unexpected_file_row`,
     `cardinality_violation` (honouring one / zero_or_one / many).
  8. Re-iterates (gated on a non-zero unknown count) to collect
     `unknown_record_type` violations with line numbers.
  9. Evaluates cross-type assertions against parsed file rows.

  **Public surface:** `ReconciliationViolation`, `ReconciliationReport`,
  `PerTypeCount` (frozen dataclasses); `DbTruthComparatorError`
  (single exception type).

  **Comparison semantics:** strict trimmed-string equality;
  `NULL → ""`; `regression_only=True` skips comparison but still
  counts the row; per-field/per-record-type SQL predicates are
  applied **SQL-side** (the engine does not re-evaluate them).

  **Assertion grammar (narrow on purpose):**
  ```
  expr := <side> '==' <side>
  side := <int_literal>
        | 'header' '.' <field_name>
        | 'trailer' '.' <field_name>
        | 'sum' '(' 'detail_row_counts' ')'
        | 'count' '(' <record_type_name> ')'
  ```
  Operators other than `==` raise at call time. Arbitrary Python is
  never `eval()`ed. The `header`/`trailer` anchors are resolved via
  the umbrella's `position=first`/`position=last` record types so
  the grammar is portable across sources.

  **Determinism:** violations sort by `(record_type, line_number,
  kind_order, field, key_values)`. Per-record-type violations
  precede the cross-cutting `assertion_failed` and
  `unknown_record_type` block.

- `tests/unit/test_e2e_db_truth_comparator.py` — 24 tests across 10
  classes. PEP-249 connection stubbed via `MagicMock` with a
  **SQL-substring-matching cursor stub** (tests don't have to
  hardcode the engine's spec iteration order). Synthetic
  2-record-type fixture (HDR `position=first` + DET match-based).
  See §6.2 for the full case list.

**Quality gates at commit time:**
- 24/24 new tests pass, exit code 0.
- 138/138 prior #17/#21 tests still pass (six suites: 162 total
  across all six).
- `black` clean on both files.
- `mypy --follow-imports=silent --explicit-package-bases` clean on
  the new module apart from the pre-existing missing `types-PyYAML`
  stubs (already affecting `reconciliation_spec.py` from commit 1).
- Full-suite delta: trunk baseline `30 failed, 2383 passed` → with
  commit 3 in place `30 failed, 2407 passed`. **Zero regressions;
  +24 = my new tests.**

### 4.4 Merge to trunk

```powershell
git checkout feature/issue-11-kill-file-search
git merge --ff-only feature/issue-17-l2b-sql-truth-engine
git push origin feature/issue-11-kill-file-search
git branch -d feature/issue-17-l2b-sql-truth-engine
```

Fast-forward `9172f5e..9f24172` (8 files, 4,438 insertions including
the session-2 commit-1 work, which was on the feature branch but not
yet on trunk). Pushed to origin. Feature branch deleted locally.

## 5. Resume sequence — do these in order

### 5.1 Verify state

```powershell
git checkout feature/issue-11-kill-file-search
git log -1 --oneline
# Expect: 9f24172 feat: add db_truth_comparator engine
```

If the head is not `9f24172`, stop and investigate before continuing.

### 5.2 Start #18 — SHAW TRANERT SQL artifacts

[#18](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues/18)
is **scripts + config only** (no `src/` touches). Plan per the issue
description and session-2 §3.5:

- Author the SQL artifacts under `config/e2e/sources/SHAW/sql/tranert/`:
  - `00_bootstrap/` — `CREATE OR REPLACE VIEW`s for the staging
    schema, lookup helper tables (`CREATE TABLE IF NOT EXISTS`), any
    sequences needed.
  - `10_load/` — `MERGE` from lookup CSVs into helper tables.
  - `20_query/expected_<record_type>.sql` — one file per record type
    the spec reconciles (32000, 32005, 32010, 32015, 32020, 32025,
    plus header / trailer types per the SHAW TRANERT spec).

- Ground truth lives in the Java files
  `config/templates/DAOOperations (2).java` and
  `config/templates/TranertMapper (1).java`. **These remain
  untracked, intentionally** — they are read-only references the
  user has chosen not to commit. Do not stage them.

- The 13-correction list on
  [#17](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues/17)
  (session-2 §5) is the canonical reference for what each record
  type's SQL must produce. Re-read it before authoring 32010 (ORI
  conditional suppression), 32005 (per-contact composite key), and
  32025 (six additional directly-testable fields).

- AGENTS.md hard rule #1 applies in full to #18: this is **harness
  config only**, no `src/` modifications. The carve-out is reserved
  for primitive extractions; SQL authoring does not qualify.

- Branch: create `feature/issue-18-shaw-tranert-sql` from trunk
  `9f24172`. Same single-developer fast-forward workflow as #17 and
  #21 (no MR review process in play).

### 5.3 Stretch: ADR 0008 status flip

ADR 0008 (`docs/adr/0008-extract-multi-record-reader-primitive.md`)
currently reads `Proposed (flip to Accepted on MR merge)`. Since
#21 was merged via fast-forward (no MR), the user may want to flip
this to `Accepted — Implemented 2026-05-27` (or `2026-05-28` matching
the actual session-4 / session-5 boundary) as a tiny `docs:` commit.
Not blocking #18.

### 5.4 Continue with #19 → #20

Per [#19](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues/19)
and
[#20](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues/20).
Each gets its own branch and its own commit set. #20 includes the
infographic-tab pill flip (session-2 §7.4) — only flip when #20
actually closes, not earlier.

## 6. Useful context for the next session

### 6.1 Engine surface area — quick reference

The L2b gate's Python entry point is one function:

```python
from scripts.e2e_lib.db_truth_comparator import reconcile
from scripts.e2e_lib.reconciliation_spec import load_spec

spec = load_spec(Path("config/e2e/sources/SHAW/reconciliation/tranert.yml"))
report = reconcile(conn, spec, output_file_path)

if report.violations:
    # gate FAIL
    ...
```

Report attributes (frozen, see commit 3): `spec_source`,
`spec_file_type`, `rows_compared`, `rows_unknown_type`,
`violations: Tuple[ReconciliationViolation, ...]`,
`per_type_counts: Mapping[str, PerTypeCount]`.

Violation kinds emitted by the engine: `field_mismatch`,
`missing_expected`, `unexpected_file_row`, `cardinality_violation`,
`assertion_failed`, `unknown_record_type`.

### 6.2 Engine test inventory — full case list

`tests/unit/test_e2e_db_truth_comparator.py` (24 tests, 10 classes):

| Class | Tests | What it covers |
|---|---|---|
| `TestHappyPath` | 1 | Two record types, all fields match → empty violations; per-type counts correct |
| `TestFieldMismatch` | 1 | One field differs on one row → exactly one `field_mismatch` with `record_type`, `field`, `expected`, `actual`, `key_values`, `line_number` |
| `TestMissingAndUnexpectedRows` | 2 | SQL-only key → `missing_expected`; file-only key → `unexpected_file_row` with line number |
| `TestCardinality` | 3 | `ONE_PER_DRIVER_ROW` with duplicated file row; `ZERO_OR_ONE_PER_DRIVER_ROW` with two file rows at same key; `MANY_PER_DRIVER_ROW` with matched multiset is clean |
| `TestRegressionOnlyAndIgnored` | 2 | `regression_only=True` difference is not a violation; `ignored_fields` produce no violations |
| `TestAssertions` | 4 | Happy path; mismatch emits `assertion_failed`; unsupported operator (`!=`) raises at call time; unparseable side (`len(file)`) raises; non-integer header field raises |
| `TestBootstrapOrchestration` | 2 | `skip_bootstrap=True` suppresses both bootstrap and load (verified by deliberately broken bootstrap SQL not being reached); `skip_bootstrap=False` runs bootstrap → load → expected SQL in that order |
| `TestUnknownRecordType` | 2 | `default_action="warn"` emits `unknown_record_type` violations with line numbers; `default_action="error"` translates parser raise to `DbTruthComparatorError` |
| `TestViolationOrdering` | 1 | Mixed kinds emit in stable sort order; per-record-type violations precede assertion failures |
| `TestReportTypesAreFrozen` | 3 | `PerTypeCount`, `ReconciliationViolation`, `ReconciliationReport` reject mutation |
| `TestConfigurationDefects` | 2 | Missing expected SQL file and missing umbrella mapping both raise `DbTruthComparatorError` |

### 6.3 Things to know about #18

- Service account has **DDL + temp-table privileges in SIT and AIT**
  (session-2 §8 confirmation). Idempotent bootstrap is possible.
- Oracle is reached via `python-oracledb` in thin mode by default
  (AGENTS.md environment notes).
- The `expected_*.sql` files do **not** receive bind parameters from
  the engine (session-5 §3 decision). Anything the SQL needs must be
  hard-coded or computed via the staging schema in scope.
- `expected_*.sql` files **are read as text**, not parsed. They go
  straight to `cursor.execute(sql_text)`. So multi-statement files
  are fine for `bootstrap_dir` and `load_dir` (the splitter handles
  them) but for `query_dir/expected_*.sql` keep each file to a
  single `SELECT` — the engine sends the whole text as one
  statement.
- Cardinality drives reconciliation behaviour. Per the 13-correction
  list, 32010 is `zero_or_one_per_driver_row` with predicate
  `CHG_OFF_CD = '1'`; 32005 is `many_per_driver_row` with composite
  key `(ACCT_NUM, CONTACT_ID)`. The SQL for each must encapsulate
  the predicate (Python side does not re-evaluate).

### 6.4 Things to know about #19

- The reconciliation YAML for SHAW TRANERT goes at
  `config/e2e/sources/SHAW/reconciliation/tranert.yml` (or wherever
  #19's description specifies — confirm at start of #19).
- The YAML shape is documented in
  `scripts/e2e_lib/reconciliation_spec.py` lines 30-65 (the
  reference example) and validated by `load_spec()`.
- Cross-reference the existing `MultiRecordConfig` umbrella at
  `config/mappings/SHAW_TRANERT.yaml` for record-type names — they
  must match exactly between the umbrella and the reconciliation
  spec's `record_types` keys.
- Each `FieldSpec.file_field` must match a name declared in the
  per-record-type mapping JSON (the umbrella's `record_types[X].mapping`
  → e.g. `config/mappings/SHAW_TRANERT_CUS_mapping.json`).
- Each `FieldSpec.expected_column` must match a column returned by
  the corresponding `expected_*.sql` (case-sensitive; Oracle returns
  upper-case by default).

## 7. Carried-forward items (still open)

### 7.1 `stash@{1}` — still untouched

Same status as sessions 2, 3, 4. Contents documented in session-2 §6
(conftest unicode fix already landed in session 4 — that part of the
stash is now redundant; `src/api/*` WIP and scratch files remain
unresolved). Recommendation unchanged: do not touch without
explicit user direction. The unicode fix portion can be dropped
from the stash if the user wants to clean it up; the `src/api/*`
portion needs a user call on whether it's still WIP they care about.

### 7.2 ADR 0008 status field

Still reads `Proposed (flip to Accepted on MR merge)`. Tiny `docs:`
commit recommended. See §5.3.

### 7.3 Pre-existing trunk test failures (30 in 7 files)

Documented in session-4 §4.5 and confirmed unchanged in session-5
regression checks. Files: `test_alembic_install`,
`test_chunked_validator_stats`, `test_downloader_service`,
`test_strict_mode_parity`, `test_trend_service`, `test_web_ui`,
`test_workflow_engine`. None touch any L2b module. Recommended
disposition: out-of-scope for L2b work; address in a separate
maintenance MR when convenient.

### 7.4 `.flake8` config bug

Session-4 §6.4 #2 documented this: `ignore=` value contains a `#`
comment which modern flake8 rejects, breaking flake8 everywhere.
One-line fix in `.flake8`. Not blocking L2b. Recommended: standalone
`fix(lint):` commit at any point.

### 7.5 mypy whole-tree cleanup

Session-4 §6.4 #3 documented 93 errors across the wider tree.
Out of scope for L2b. Worth a dedicated MR.

### 7.6 Vestigial validator methods

`MultiRecordValidator._extract_discriminator` and
`._identify_record_type` are still kept with `TODO(#21-followup)`
markers. ADR 0008's "Open questions deferred" section recommends
removal in a follow-up issue. Not blocking #18.

### 7.7 Untracked working-tree files

Still present, still intentional:
- `config/templates/DAOOperations (2).java` (Java ground truth)
- `config/templates/TranertMapper (1).java` (Java ground truth)
- `mappings/excel/~$TRANERT_SHAW_Mappings.xlsx` (Excel lock file)

These do not interfere with branch switching or merges.

## 8. Things this session learned that weren't in session 4

### 8.1 `MappingDocument` vs `UniversalMappingParser` — different shapes

When commit 2 of #17 needed per-record-type field slicing, I
initially assumed `src/config/mapping_parser.py::MappingDocument` was
the right primitive because it is the canonically-named "mapping"
loader. It is not — its `ColumnMapping` has no `position` or
`length` attributes; it is dataframe-oriented.

The correct primitive is
`src/config/universal_mapping_parser.py::UniversalMappingParser`. Its
`FieldSpec` carries `position` (1-indexed) and `length`. The
parser's constructor takes `mapping_path: str`. This is what the
L2b file parser uses.

Recorded so the next session does not repeat the lookup.

### 8.2 Tiny YAML emitters and Windows paths don't mix

A test fixture in commit 3 initially used a hand-rolled YAML emitter
that double-quoted every string. Windows paths (`C:\Users\…`) embed
`\U` and `\T` sequences that YAML's double-quoted scalar grammar
interprets as escape codes. Result: 18/24 commit-3 tests failed at
fixture-load time with errors about "expected escape sequence of 8
hexadecimal numbers."

The fix was trivial: use `yaml.safe_dump`. The lesson is similar to
session 4 §7.3 ("always check `scripts/` before writing a new
script"): **use the existing library instead of hand-rolling**, even
when "the data is simple."

### 8.3 SQL-substring-matching cursor stubs are more robust than positional

The first iteration of the engine test fixtures used positional
rowsets (the Nth `cursor.execute` call gets the Nth canned
rowset). This was brittle: when the engine's spec iteration order
changed for any reason, the wrong rowset would surface.

Session 5 settled on a `_conn_with_rowmap` helper where the cursor's
`execute` side-effect matches the SQL text against a dict of
substring → rowset. The fixture only has to know what each SQL
file's distinctive marker is (a view name like `expected_header_view`
or `expected_detail_view`), and the matching is order-independent.
This pattern should be reused in #18-related tests and any future
engine work.

### 8.4 Multi-paragraph commit messages on Windows: write-to-temp-file works

Sessions 3 and 4 documented the Windows shell pain points; session 5
inherited them. Workaround that worked every time:

1. Write the commit message to a workspace-relative temp file
   (e.g. `_commit_msg_tmp.txt`) via Duo's `create_file_with_contents`
   (NOT under `.git/` — Duo's context exclusions block that path).
2. `git commit -F _commit_msg_tmp.txt`.
3. `del _commit_msg_tmp.txt` after the commit lands.

`$env:TEMP\…` paths via Duo's tool do not work (the tool resolves
absolute paths relative to the workspace), so a workspace-relative
temp file is the simplest option. Add it to `.gitignore` if it ends
up being a regular pattern.

### 8.5 The `--follow-imports=silent --explicit-package-bases` mypy invocation

Stripping out the noise from transitive `src/` imports (which carry
pre-existing typing debt) plus telling mypy not to double-discover
`scripts.e2e_lib` and `e2e_lib` is what produces an actionable
output for new code. Standard incantation for #18-onwards:

```powershell
python -m mypy --follow-imports=silent --explicit-package-bases <new-file>
```

The only remaining noise will be the missing `types-PyYAML` stubs,
which already affects `reconciliation_spec.py` and `db_truth_comparator.py`.
Suppress with `# type: ignore[import-untyped]` on the `import yaml`
line if it becomes annoying; deferred until then.

## 9. Decisions log — combined sessions 1–5

Cross-reference: session-4 §8 has the first nineteen. Session 5 added:

| Decision | Why | Where it's documented |
|---|---|---|
| `stash@{0}` dropped at session start | Empty baseline-capture leftover, no value | This doc §3 |
| `UniversalMappingParser` is the right primitive for field slicing | `MappingDocument` is dataframe-oriented; `FieldSpec.position`/`length` live in `UniversalMappingParser` | This doc §3, §8.1 |
| Driver-query model: join-on-key against each per-type expected SQL | Spec doesn't model a separate driver query; existing surface area matches | This doc §3 |
| No bind parameters into expected SQL in this release | Spec doesn't model them; future field + ADR if needed | This doc §3, §6.3 |
| Assertion grammar evaluates against file-side rows only | Spec examples reference file-side anchors; SQL-side is out of scope | This doc §3 |
| Umbrella loaded inside `reconcile()`, errors translated to `DbTruthComparatorError` | Caller catches one error type | This doc §3 |
| Single-developer fast-forward for #17 → trunk merge | User is sole developer; matches session-4 #21 pattern | This doc §3, §4.4 |
| Delete `feature/issue-17-l2b-sql-truth-engine` after merge | Work is on trunk; the branch is dead weight | This doc §3, §4.4 |
| Substring-matching cursor stub for engine tests | Order-independent; resilient to engine internals | This doc §8.3 |
| Use `yaml.safe_dump`, not hand-rolled YAML emitters | Backslash-handling correctness | This doc §8.2 |

## 10. Open questions for the next session

These were not resolved during session 5:

1. **#18 scope detail.** Are the per-record-type `expected_*.sql`
   files expected to live at `config/e2e/sources/SHAW/sql/tranert/20_query/expected_<record_type>.sql`,
   or somewhere else? Cross-check with #18's description at session
   start.
2. **Lookup CSVs for `10_load/`.** The TRANERT Java does
   property-file lookups (per the 13-correction list and session-2
   §5). Do these property files exist as CSVs in the repo today, or
   does #18 also include extracting them from the Java source?
3. **ADR 0008 status flip.** Cosmetic; not blocking #18. Should this
   be done now as a one-line `docs:` commit, or rolled into #18?
4. **`.flake8` config bug.** Standalone `fix(lint):` commit before
   #18, or after, or skipped?
5. **`stash@{1}` final disposition.** Same question as sessions
   2–4. Not blocking #18 work but worth resolving.
6. **MR vs fast-forward for #18.** Session 5 confirmed
   single-developer fast-forward for #17 (matching #21). Confirm
   this remains the workflow for #18–#20.
7. **#20 infographic pill flip.** Per session-2 §7.4, the
   architecture-tab pills flip from `designed/planned/not yet built`
   → `wired` when L2b actually lands. Confirm this still applies
   when #20 is picked up.

## 11. Quick links

- [#17 — L2b engine + SQL bootstrap](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues/17) — **all 3 commits landed and merged to trunk in session 5**
- [#18 — SHAW TRANERT SQL artifacts](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues/18) — **unblocked; next**
- [#19 — SHAW TRANERT reconciliation YAML + wiring](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues/19)
- [#20 — tests + CI + infographic pill flip](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues/20)
- [#21 — refactor: extract multi-record reader primitive](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues/21) — **merged in session 4**
- [`docs/handover/L2B_SESSION_4_HANDOVER.md`](L2B_SESSION_4_HANDOVER.md) — predecessor
- [`docs/handover/L2B_SESSION_3_HANDOVER.md`](L2B_SESSION_3_HANDOVER.md)
- [`docs/handover/L2B_SESSION_2_HANDOVER.md`](L2B_SESSION_2_HANDOVER.md)
- [`docs/adr/0008-extract-multi-record-reader-primitive.md`](../adr/0008-extract-multi-record-reader-primitive.md) — ADR; status still `Proposed (flip to Accepted on MR merge)`
- `docs/handover/TRANERT_DB_TESTING_SESSION_SUMMARY.md` — session-1 design doc (still stashed)
- `docs/Valdo-Infographic.html` Architecture tab → "E2E harness gates" section
- Java ground truth (untracked, intentional): `config/templates/TranertMapper (1).java`, `config/templates/DAOOperations (2).java`
- SHAW smoke-test fixtures (committed): `data/samples/atoctran_shaw_20260514.txt`, `data/samples/tranert_shaw_20260422.txt`
- L2b engine entry point: `scripts/e2e_lib/db_truth_comparator.reconcile()` — see §6.1 for usage
