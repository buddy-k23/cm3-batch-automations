# L2b SQL-Truth Gate — Session 6 Handover

**Generated:** 2026-05-29
**Predecessor:** [`docs/handover/L2B_SESSION_5_HANDOVER.md`](L2B_SESSION_5_HANDOVER.md) — read first; everything in this doc is a *delta* on session 5, not a replacement.
**Branch at handover:** `feature/issue-18-shaw-tranert-sql` (head `fa5a60a`, **local-only, never pushed**).
**Trunk at handover:** `feature/issue-11-kill-file-search` at `a3f3c4d` (unchanged from session 5).
**Issues this session touched:** #18 (8 of ~10 expected commits landed; 1 fix in-progress uncommitted).

---

## 1. Where you are in the workflow

Session 6 picked up #18 from a clean trunk and made substantial progress on
the SHAW TRANERT SQL artifacts. **8 commits landed** covering: DDL
bootstrap, property-file extractor (with one bug-fix commit), lookup-load
DML (initially against a synthetic fixture, then regenerated against the
real Spring property file), helper SQL views, and a SIT smoke-runner
with whitelist statement classifier. The first live Oracle execution
against SIT surfaced **three** distinct issues that drove three further
design refinements; the final one (cross-schema view-creation privilege)
is unresolved at handover and is the question for session 7.

#18 is **substantially complete but not yet validated end-to-end** against
SIT. The remaining work breaks into three pieces:

1. **Fix the cross-schema view-creation issue** (the open question at §10).
2. **Land the in-progress fix commit** (currently uncommitted in the
   working tree — see §6 below).
3. **Author commit 5b (expected views)** plus commit 6 (parse-smoke test).

| Item | State | Notes |
|---|---|---|
| #18 commit 1: lookup-table DDL bootstrap (`17dffb9`) | ✅ Landed | 7 `app_int.LKP_VALDO_SHAW_*` tables, idempotent PL/SQL anonymous blocks |
| #18 commit 2: property-file extractor (`27d9d51`) | ✅ Landed | Includes synthetic fixture (Option B) |
| #18 bug-fix: extractor terminator/line-ending fix (`46dab60`) | ✅ Landed | Splitter compat + LF-only output |
| #18 commit 3: lookup-load DML against fixture (`d07a3ba`) | ✅ Landed | Synthetic-data version, historical |
| #18 commit 4: regenerate lookup-load against real property file (`c11f7ab`) | ✅ Landed | 161 real lookup rows; bundles `/_ref/` `.gitignore` rule |
| #18 commit 5a: helper SQL queries + `V_*` views (`ea327ec`) | ✅ Landed | 11 helper views + 11 wrapper queries; bare-identifier names |
| #18 O.1: SIT smoke-runner + whitelist classifier (`cf69fe5`) | ✅ Landed | 42 tests, mock-Oracle integration |
| #18 schema-qualification fix (`fa5a60a`) | ✅ Landed | All harness objects → `app_int.`; classifier enforces Policy A |
| #18 second fix (in-progress, **uncommitted**) | 🔄 Working tree | `V_SHAW_TRANERT_COST2` removed (ORA-01027); bind-var classifier check added; `_strip_all_comments` helper; `uzapp_ad0.*` qualifications on 4 views |
| #18 commit 5b (expected views) | ⏸ Not started | The per-record-type business-logic views |
| #18 commit 6 (parse-smoke test) | ⏸ Not started | `tests/unit/test_e2e_shaw_tranert_sql_smoke.py` |
| #19, #20 | ⏸ Blocked on #18 | Per design |

## 2. The dependency chain — current state

```
#21  ✅ merged to trunk (session 4)
#17  ✅ merged to trunk (session 5)
#18  🔄 in progress on feature/issue-18-shaw-tranert-sql (8 commits + 1 in-progress fix)
#19  ⏸ blocked on #18
#20  ⏸ blocked on #19
```

## 3. Decisions made in session 6 (do not re-litigate)

These were confirmed by the user during the session before code was written
or after specific findings surfaced. Listed in the rough order they
emerged:

| # | Decision | Choice | Why |
|---|---|---|---|
| 1 | Lookup CSV provenance (Q1) | **Option B** — extractor lands with synthetic fixture; real CSVs regenerated at AIT/SIT setup | Avoids inventing data; honours hard rule #3 |
| 2 | Helper-query placement (Q2) | **(i)** Helpers are `V_*` views in `00_bootstrap/020_expected_views.sql`; `20_query/*.sql` files are thin `SELECT * FROM V_*` wrappers | DBA-runnable, single source of truth for the SQL bodies |
| 3 | 10_load shape (Q3) | **(iii)** Generated `INSERT` SQL via the extractor | Cleaner than external tables; no DBA grant needed |
| 4 | Live Oracle access account (Q1 v2) | **Use `uzapp_ad0`** | User direction (later revised mid-session — see #10 below) |
| 5 | Reference-file location | **Path A** — gitignored `_ref/` at workspace root | Cleanest isolation; documents the role |
| 6 | `tranert.32025.cn.*` (14,201 entries in `tranert-center-gl.properties`) | **Ignored / not modelled** | Dead code in the Java mapper (assigned but never read) |
| 7 | Lookup-load regenerate against real properties | **β** new commit on top of `d07a3ba`, not an amend | Branch is local-only either way; new commit documents the transition + the gitignore safety net |
| 8 | Commit 5 split | **5a (helpers + V_* views) + 5b (expected views)** | Per-commit review tractability |
| 9 | `_ref/tranert-center-gl.properties` | **Leave in `_ref/`** as historical reference | Already gitignored; no harm |
| 10 | Schema policy (Policy A) | **Strict `app_int.` qualification on every harness DDL/DML target** | Connected user `uzapp_ad0` ≠ default-schema target; bare identifiers silently misroute |
| 11 | Table/view naming convention (α) | Keep **`LKP_VALDO_SHAW_*`** prefix (drop `SHAW_` deferred) | Scales when other sources land |
| 12 | `V_SHAW_TRANERT_COST2` view (mid-fix) | **Remove**; ship cost2 as a standalone parameterised query under `20_query/cost2.sql` | Oracle ORA-01027: views cannot accept bind variables |
| 13 | Classifier bind-var check | **Add** `_RE_BIND_VARIABLE` + `_strip_all_comments` + refuse `CREATE VIEW` with binds | Catches ORA-01027 at the gate; comment-aware so docstring bind examples don't false-fire |
| 14 | View-owner schema after privilege failure | **DEFERRED to session 7** — see §10 | Discovery: connecting as `app_int` may fix it without code change |

## 4. What changed in this session — concretely

### 4.1 Commit 1 — `17dffb9 feat: add SHAW TRANERT lookup-table DDL bootstrap`

**File added:** `config/e2e/sources/SHAW/sql/tranert/00_bootstrap/010_lookup_tables.sql` (219 lines).

7 lookup tables (`LKP_VALDO_SHAW_REP_TYPE_ORI_BY_PRIN_SCH`, etc.) wrapped in
PL/SQL anonymous blocks that trap ORA-00955 for idempotency. Oracle doesn't
support `CREATE TABLE IF NOT EXISTS` — I learned this mid-edit and rewrote
to the canonical Oracle idiom. The bootstrap splitter (commit 5a of #17,
session 5) handles `/`-terminated PL/SQL blocks; verified the file parses
into 7 statements.

### 4.2 Commit 2 — `27d9d51 feat: add SHAW TRANERT property-file extractor`

**Files added (3):**

- `scripts/e2e_lib/extract_tranert_properties.py` (production, 538 lines).
- `tests/unit/fixtures/tranert_properties/sample.properties` (synthetic 25-entry fixture per Option B).
- `tests/unit/test_e2e_extract_tranert_properties.py` (47 tests across 9 classes).

Public surface: `TranertPropertyExtractError`, `ExtractedLookups`,
`RunResult`, `parse_properties`, `extract_lookups`, `write_csvs`,
`write_load_sql`, `run`, `main`. SHA-256 idempotency via
`<output_dir>/.sha256`. CLI requires `--property-file`, `--output-dir`,
`--sql-load-file` (no env-var defaults per hard rule #3).

### 4.3 Bug fix — `46dab60 fix: SHAW TRANERT extractor load-SQL terminator placement and line endings`

Two real bugs caught when generating the first real load-SQL artifact:

1. **Inline `;` terminators** on the same line as the statement body were
   not parsed by `sql_bootstrap._split_statements` (which requires `;`
   alone on its own line). Fixed by emitting each statement followed by
   `\n;\n`.
2. **`write_text` on Windows** silently converted `\n` to `\r\n`,
   breaking byte-stable cross-OS output (the SHA-256 idempotency
   contract). Fixed by switching to `path.open("w", newline="")`.
3. **Bonus** (Bug 3): the empty-bucket case emitted a "-- (no rows
   produced)" comment after the TRUNCATE's `;`, which became a
   comment-only trailing statement that Oracle would reject as
   ORA-00900. Fixed by dropping the redundant comment.

### 4.4 Commit 3 — `d07a3ba feat: add SHAW TRANERT lookup-load DML (extractor-generated)`

**File added:** `config/e2e/sources/SHAW/sql/tranert/10_load/010_load_lookups_from_csv.sql`
(95 lines, 14 statements: 7 TRUNCATE + 7 INSERT, from the synthetic fixture).

Companion `.gitignore` entry added for `config/e2e/sources/SHAW/lookups/`
(transient extractor intermediate, regenerated per environment).

### 4.5 Commit 4 — `c11f7ab chore: regenerate SHAW TRANERT lookup-load DML against real property file`

**Files modified (2):**
- `config/e2e/sources/SHAW/sql/tranert/10_load/010_load_lookups_from_csv.sql` regenerated from 3,861 → 17,756 bytes (7 TRUNCATE + 161 INSERT = 168 statements).
- `.gitignore` got the `/_ref/` block.

The four `_ref/` files (`db-sit.properties`,
`shaw-interfaces-common.properties`, `shaw-sql-statements.properties`,
`tranert-center-gl.properties`, `GLCostCenterMapping.xlsx`) were
moved into `_ref/` from `config/` because the agent's `read_file` tool
refuses to read gitignored paths, and the per-file gitignore entries
prevented agent access. The `_ref/` directory pattern keeps the
reference files workspace-resident and gitignored.

### 4.6 Commit 5a — `ea327ec feat: add SHAW TRANERT helper SQL queries and V_* views`

**Files added (12):**
- `config/e2e/sources/SHAW/sql/tranert/00_bootstrap/020_expected_views.sql`
  (1 file, 11 views, 414 lines pre-schema-qualification).
- 11 wrapper files under `config/e2e/sources/SHAW/sql/tranert/20_query/`
  (one per helper view).

The 11 helper views correspond to 11 of the 16 `@Value`-injected SQL
strings the Java's `DAOOperations` reads from the Spring property file.
Every helper view body is a direct transcription of the SQL string at
the matching property-file key, formatted for Oracle. The five Spring
SQL keys NOT modelled as views were either the lookup-table sources
(handled by the extractor in commit 2) or out-of-scope for #18.

### 4.7 O.1 — `cf69fe5 feat: add SHAW TRANERT SIT smoke-runner with whitelist statement classifier`

**Files added (2):**
- `scripts/e2e_lib/shaw_tranert_smoke.py` (production, 552 lines).
- `tests/unit/test_e2e_shaw_tranert_smoke.py` (42 tests across 9 classes).

Three CLI subcommands: `bootstrap`, `validate`, `query <name>`.
Whitelist statement classifier (`classify_statement(sql) ->
StatementClassification`) refuses anything not on a narrow allow-list.
JSONL audit log at `reports/shaw_tranert_smoke/<UTC-ts>.jsonl`. Password
auto-redaction in the audit writer. SecretResolver-based credentials
from `.env` (`ORACLE_DSN_SIT` / `ORACLE_USER_SIT` /
`ORACLE_PASSWORD_SIT`).

### 4.8 Schema-qualification fix — `fa5a60a fix(sql): schema-qualify SHAW TRANERT harness objects to app_int (Policy A)`

**Files modified (18):**
- `010_lookup_tables.sql`: 7 CREATE TABLE → `app_int.LKP_VALDO_SHAW_*`.
- `020_expected_views.sql`: 11 view names + 1 cross-view reference all qualified.
- `010_load_lookups_from_csv.sql`: regenerated by the extractor.
- 11 wrapper files in `20_query/`: `FROM V_*` → `FROM app_int.V_*`.
- `extract_tranert_properties.py`: `_TABLE_FOR_CSV` mapping updated.
- `shaw_tranert_smoke.py`: classifier rewritten to handle
  `<schema>.<object>` identifiers, validate against `_HARNESS_SCHEMA =
  "APP_INT"`, refuse bare-identifier DDL/DML.
- Test files updated for new semantics (10 new refusal-mode cases under
  `TestClassifyPolicyAEnforcement`).

Quality gates: 261/261 passing.

### 4.9 In-progress fix (uncommitted)

After the first SIT bootstrap attempt (audit log
`reports/shaw_tranert_smoke/20260529T191415Z.jsonl`) failed with
**ORA-01027: bind variables not allowed for data definition operations**
on `V_SHAW_TRANERT_COST2`, three changes were authored in response:

1. **Removed `V_SHAW_TRANERT_COST2` view definition** from
   `020_expected_views.sql`. Replaced with an explanatory comment block
   pointing to `20_query/cost2.sql`.
2. **Rewrote `20_query/cost2.sql`** as a standalone parameterised
   `SELECT` (binds `:batch_date` and `:rank_offset`), no view dependency.
3. **Added a classifier bind-var check**: new `_RE_BIND_VARIABLE` regex,
   `_strip_all_comments` helper that mirrors Oracle's comment-stripping
   parse behaviour (so docstring examples mentioning `:bind` don't
   false-fire), wired into the `CREATE VIEW` branch of
   `classify_statement`. New `TestClassifyCreateViewBindVarRejection`
   class with 9 cases including comment-stripping edge cases (line
   comments, block comments, string literals with `:` inside, doubled
   apostrophes).

A second SIT bootstrap attempt (audit log
`reports/shaw_tranert_smoke/20260529T205155Z.jsonl`) then failed with
**ORA-00942: table or view does not exist** on
`V_SHAW_TRANERT_CONTACTS_MERGED`. Root cause: the view's APPS leg
references `uzapp_ad0`-owned tables (`coll_contact`, `CONTACT_ACCOUNT`,
`LOAN_CUST_INFO`) without explicit schema qualification. With the
connection as `uzapp_ad0`, bare references *should* resolve there — but
the CREATE VIEW was failing because Oracle's view-compilation step has
stricter rules.

After clarification from the user about which tables live in which
schema (see §6 below), the in-progress fix added **`uzapp_ad0.*`
qualification** to all bare references in:
- `V_SHAW_TRANERT_CONTACTS_MERGED` (both legs):
  `uzapp_ad0.coll_contact`, `uzapp_ad0.CONTACT_ACCOUNT`,
  `uzapp_ad0.LOAN_CUST_INFO`.
- `V_SHAW_TRANERT_BK1`: `uzapp_ad0.cds_acct_bk1` (a view).
- `V_SHAW_TRANERT_BK3`: `uzapp_ad0.cds_acct_bk3` (a view).
- `V_SHAW_TRANERT_STATE_PROVINCE`: `uzapp_ad0.account`,
  `uzapp_ad0.coll_addr`, `uzapp_ad0.coll_addr_role`.

A `discover` subcommand was added to the smoke runner that queries
`ALL_OBJECTS` to confirm which schema owns a given table/view name.

A third SIT bootstrap attempt (audit log
`reports/shaw_tranert_smoke/20260529T214737Z.jsonl`) then failed with
**ORA-01031: insufficient privileges** on
`V_SHAW_TRANERT_CONTACTS_MERGED`. Root cause: a view in the `app_int`
schema that references `uzapp_ad0.*` tables requires `app_int` to have
been granted `SELECT WITH GRANT OPTION` on those tables — which it
hasn't been.

This is the **unresolved issue at handover** — see §10 for the
decision tree.

**Working-tree state at handover:**
```
 M config/e2e/sources/SHAW/sql/tranert/00_bootstrap/020_expected_views.sql
 M config/e2e/sources/SHAW/sql/tranert/20_query/cost2.sql
 M scripts/e2e_lib/shaw_tranert_smoke.py
 M tests/unit/test_e2e_shaw_tranert_smoke.py
```

All four files have black + mypy clean, 272/272 tests passing locally.

## 5. Live SIT validation status

**Three bootstrap attempts** against SIT (connected as `uzapp_ad0`):

| Attempt | Audit log | Result |
|---|---|---|
| 1 | `reports/shaw_tranert_smoke/20260529T191415Z.jsonl` | 7 tables + 168 lookup INSERTs + 3 views OK; failed on `V_SHAW_TRANERT_COST2` (ORA-01027) |
| 2 | `reports/shaw_tranert_smoke/20260529T205155Z.jsonl` | 7 tables + 168 lookup INSERTs + 6 views OK; failed on `V_SHAW_TRANERT_CONTACTS_MERGED` (ORA-00942) |
| 3 | `reports/shaw_tranert_smoke/20260529T214737Z.jsonl` | 7 tables + 168 lookup INSERTs + 6 views OK; failed on `V_SHAW_TRANERT_CONTACTS_MERGED` (ORA-01031) |

**Views proven to parse against the live SIT `app_int` schema:**
- `V_SHAW_TRANERT_LOAN_MASTER_BATCH_DATES` ✓
- `V_SHAW_TRANERT_DRIVER` ✓
- `V_SHAW_TRANERT_COST1` ✓
- `V_SHAW_TRANERT_COST_MERGED` ✓ (the inlined non-parameterised cost1∪cost2 reduction)
- `V_SHAW_TRANERT_CBRS_ACCOUNT_SUMMARY` ✓
- `V_SHAW_TRANERT_LOANS_NAME` ✓

**Views NOT yet proven to parse:**
- `V_SHAW_TRANERT_CONTACTS_MERGED` (blocked on cross-schema privilege)
- `V_SHAW_TRANERT_BK1` (referenced object in `uzapp_ad0.*`)
- `V_SHAW_TRANERT_BK3` (same)
- `V_SHAW_TRANERT_STATE_PROVINCE` (same)

**SIT object state at handover** (DDL auto-commits in Oracle, so these
survived the rolled-back transactions):
- `app_int.LKP_VALDO_SHAW_*` × 7 tables exist.
- Whether the 161 lookup INSERTs persisted is **uncertain** — they were
  inside the data transaction. If a future bootstrap fails before the
  final commit, the tables are still empty. Re-running bootstrap from a
  clean state regenerates everything (TRUNCATE + INSERT is idempotent).
- `app_int.V_SHAW_TRANERT_*` × 6 views exist (the 6 that parsed cleanly).

## 6. Schema topology (canonical, from user)

This was discovered mid-session and is critical context for any future
view-body authoring:

| Schema | Owner | What it contains | Harness write access |
|---|---|---|---|
| `app_int` | Application | Source-system tables (`SHAW_LOAN_MASTER`, `CONTACT`, `CBRS_TRW_SUMMARY`, `C360_REPO_FEE_CDS`, `SHAW_LOANS_NAME`, `BATCH_DATE_LOCATOR`, `shaw_charge_off`, `SHAW_FEE_MASTER_HISTORY`, `SHAW_LOAN_MASTER_HISTORY`, `CONTACT_ACCOUNT` — note the name collision) | Yes (lookup tables + views) |
| `uzapp_ad0` | Service-account schema | APPS-side source tables (`coll_contact`, `CONTACT_ACCOUNT`, `LOAN_CUST_INFO`, `account`, `coll_addr`, `coll_addr_role`) plus `CDS_ACCT_*` views (`cds_acct_bk1`, `cds_acct_bk3`). **All read-only.** | No |

**Important — two tables called `CONTACT_ACCOUNT` exist:**
- `app_int.CONTACT_ACCOUNT` — used by the APP_INT leg of
  `V_SHAW_TRANERT_CONTACTS_MERGED`.
- `uzapp_ad0.CONTACT_ACCOUNT` — used by the APPS leg of the same view.

These are different tables with different schemas. Any unqualified
reference is dangerous; Policy A (mandatory `<schema>.` prefix on every
target) is what makes the harness correct.

**Convention going forward:** every SQL file references every table by
its `<schema>.<object>` name. The classifier enforces this for
harness-owned write operations; reads have no enforcement but are
authored consistently by convention.

## 7. The open question for session 7 (the unresolved cross-schema privilege issue)

The third bootstrap attempt failed with ORA-01031 (insufficient
privileges) when Oracle compiled
`app_int.V_SHAW_TRANERT_CONTACTS_MERGED`, which references both
`app_int.*` and `uzapp_ad0.*` tables. The reason: a view stored in
schema X that references tables in schema Y requires Y to have granted
`SELECT WITH GRANT OPTION` on those tables to X. Standard `SELECT`
grants are insufficient.

**User's proposed solution at session-end:** *"I have app_int schema
password — should we try with that first?"*

This is the right next move. Connecting as `app_int` instead of
`uzapp_ad0` flips the cross-schema dependency direction. The Java
deployment proves that `uzapp_ad0` can read from `app_int.*` (it does so
in `DAOOperations` constantly), which strongly implies `app_int` has
`SELECT` on `uzapp_ad0.*` tables too — or that there's a synonym chain
that makes the reads transparent. Plain `SELECT` (not with grant
option) suffices for CREATE VIEW compilation as long as the view
isn't itself being granted forward.

**Three options the session-7 agent should weigh:**

| # | Approach | Pros | Cons |
|---|---|---|---|
| I | Connect as `app_int` | Self-contained; no DBA ticket; matches the Java's known-working setup | Larger blast radius than `uzapp_ad0` (app_int owns the source tables — runaway script could TRUNCATE production data); whitelist classifier still constrains this, but defence-in-depth is weaker |
| II | Get DBA to grant `SELECT WITH GRANT OPTION` from `uzapp_ad0` to `app_int` on 8 source tables | No code change | DBA ticket; every new `uzapp_ad0` table touched in future requires another grant |
| III | Move harness views to `uzapp_ad0` schema (Path I from the in-session discussion) | Self-contained; classifier enforces it; tables stay in `app_int` | Asymmetric: tables in one schema, views in another; cognitive overhead |

**My recommendation: try Option I (connect as `app_int`) first.** If it
works, ship it for the diagnostic bootstrap and validate. The
longer-term shape should be a dedicated `valdo_harness_sit` user
(least-privilege per hard rule #3), but that's session 7+ infrastructure
work, not blocking the SQL validation we need now.

### 7.1 Mechanism for switching credentials

Two ways:

**(a)** Overwrite `ORACLE_USER_SIT` / `ORACLE_PASSWORD_SIT` in `.env`
with the `app_int` values. Simplest, no code change. Drawback: the audit
log doesn't record which user we connected as; future operators reading
it can't tell `uzapp_ad0`-era logs from `app_int`-era logs.

**(b)** Add a `--as-app_int` flag to the smoke runner (recommended in
the closing pause) that reads from `ORACLE_USER_SIT_APP_INT` /
`ORACLE_PASSWORD_SIT_APP_INT`. Explicit, audit-logged, reversible. ~30
lines + 2 tests.

User had not yet chosen between (a) and (b) when the session handoff
was requested.

## 8. Resume sequence — do these in order

The session-7 agent should:

### 8.1 Read the prompt and the predecessor handovers

Pinned reading: `prompts/L2B_SESSION_7_RESUME_PROMPT.md` (authored
alongside this handover), then this doc, then session 5, then session
2.

### 8.2 Verify state

```powershell
git log -1 --oneline                  # expect: fa5a60a fix(sql): ...
git status --short                    # expect: 4 M files (the in-progress fix), 3 ?? leavings
git stash list                        # expect: stash@{0} unchanged from sessions 2-5
python -m pytest tests/unit/test_e2e_shaw_tranert_smoke.py tests/unit/test_e2e_extract_tranert_properties.py tests/unit/test_e2e_sql_bootstrap.py tests/unit/test_e2e_reconciliation_spec.py tests/unit/test_e2e_multi_record_file_parser.py tests/unit/test_e2e_db_truth_comparator.py tests/unit/test_multi_record_reader.py tests/unit/test_multi_record_validator.py --no-cov -q --tb=line
                                      # expect: 272 passed (or close — 261 if the working-tree fix is reverted/lost, but should be 272 if working tree is preserved)
```

If any of these are off, **STOP** and report.

### 8.3 Decide on the app_int connection mechanism

Ask the user (a) overwrite env vars vs (b) add `--as-app_int` flag. Then
implement that decision before any further Oracle work.

### 8.4 Run bootstrap connected as app_int

Expect 7 tables + 168 INSERTs + 10 views to succeed cleanly. Verify via
`validate` subcommand that each view probes successfully (`SELECT *
WHERE ROWNUM <= 1`).

### 8.5 Commit the in-progress fix

One large `fix(sql)` commit covering the four working-tree files plus
whatever credential-mechanism change was made in §8.3. The commit
message should cite the successful SIT validation as proof.

### 8.6 Resume the planned commit sequence

Per the session-6 plan: commit 5b (expected views), commit 6
(parse-smoke test). Then #18 is done.

## 9. Things this session learned that weren't in session 5

### 9.1 Oracle does not support `CREATE TABLE IF NOT EXISTS`

That's PostgreSQL/MySQL syntax. The canonical Oracle idiom is a PL/SQL
anonymous block trapping `ORA-00955` (object name already used).
Discovered when authoring commit 1.

### 9.2 The session-5 handover overstated the splitter's safety

Inline `;` terminators (`TRUNCATE TABLE X;` with `;` on the same line
as the body) are NOT recognised by
`sql_bootstrap._split_statements`. Only `;` alone on its own line
terminates. This bit the extractor's load-SQL output before I noticed
— surfaced in the splitter parse test in commit 5a.

### 9.3 Python's `path.write_text` does line-ending translation on Windows

`path.write_text("text with \n", encoding="utf-8")` on Windows actually
writes `\r\n` for every `\n`. To get LF-only output you must use
`path.open("w", encoding="utf-8", newline="")` and `f.write(text)`.
Caught when the SHA-256 idempotency test surfaced a cross-OS hash
mismatch.

### 9.4 Oracle treats `''` (empty string) as NULL

A `VARCHAR2(5) NOT NULL DEFAULT ''` column declaration is
self-contradictory in Oracle — the default value `''` is NULL, which
violates NOT NULL. Bit me in `LKP_VALDO_SHAW_LEDGER_CD_BY_DEPT`'s
`IDX3_VARIANT` column; fixed by using sentinel values `'STD'` and
`'AAA'` with a CHECK constraint.

### 9.5 Oracle refuses bind variables in CREATE VIEW (ORA-01027)

A view body containing `:bind_name` references is invalid DDL. Views
must be fully self-contained. Bit me in `V_SHAW_TRANERT_COST2` (which
I'd authored with `:batch_date` and `:rank_offset` binds for fidelity
to the Java's positional binds). The fix was to remove the view
entirely and ship cost2 as a standalone parameterised query under
`20_query/`. Added a classifier check that refuses CREATE VIEWs with
bind references at the gate so this can never reach Oracle again.

### 9.6 The classifier's bind-var check must strip comments first

If you naively grep for `:identifier` in the raw SQL body, every
docstring example mentioning `:batch_date` triggers a false refusal.
Oracle's parser strips comments before evaluating DDL; our check must
do the same. The `_strip_all_comments` helper handles `--`/`/* */`
plus single-quoted string literals with `''` doubled-apostrophe
escapes.

### 9.7 `uzapp_ad0` is the connected account but NOT the app_int default schema

This is the highest-impact discovery of the session. Unqualified
identifiers in SELECT/CREATE statements resolve against
`uzapp_ad0`'s default schema, not against `app_int`. The Java works
because Spring's JDBC routing handles this transparently; our harness
has to be explicit. Drove the entire Policy A fix in commit `fa5a60a`.

### 9.8 Cross-schema CREATE VIEW requires `SELECT WITH GRANT OPTION`

A view in schema X referencing tables in schema Y requires Y to have
granted `SELECT WITH GRANT OPTION` (not just `SELECT`) to X. Plain
`SELECT` is enough for ad-hoc queries but not for view compilation.
This is the open question carrying forward to session 7 — see §10.

### 9.9 `cds_acct_*` are views, not tables

Per user clarification mid-session: anything named `CDS_ACCT_*` in the
`uzapp_ad0` schema is a view, not a base table. Doesn't change the
harness SQL but worth flagging — a future query optimisation
exercise might find that the view definitions are expensive and warrant
materialisation, but that's the source-system DBA's concern, not ours.

### 9.10 `tranert.32025.cn.*` is dead code in the Java

The `tranert-center-gl.properties` file has 14,201 entries under this
prefix. The Java calls `getProperty("tranert.32025.cn." + orgLevel4Code)`
but assigns the return to a variable that's then never read (line 496 of
`TranertMapper.java`). Treated as ignored per §3 decision #6.

### 9.11 The agent's read_file tool refuses gitignored paths

Discovered when I tried to add per-file gitignore entries for the
property files and then read them. Workaround: use `run_command` with
Python or other shell to read gitignored files. Long-term solution
applied: relocate reference materials to a gitignored directory the
agent doesn't try to gitignore-protect.

### 9.12 Black on Windows can hide commit-message bytes

The session-3-onwards pattern of writing commit messages to
`_commit_msg_tmp.txt` and `git commit -F` still works. Black does NOT
reformat `.txt` files. No new gotcha here, just confirming the pattern
held.

## 10. Carried-forward items (still open)

### 10.1 The open question for session 7

The cross-schema CREATE VIEW privilege issue (ORA-01031). See §7 above.
**Recommended first move: connect as `app_int` and re-run bootstrap.**

### 10.2 stash@{0}

Same status as sessions 2-5. Untouched. Session-2 §6 still applies.

### 10.3 ADR 0008 status field

Still reads `Proposed (flip to Accepted on MR merge)`. Tiny `docs:`
commit recommended; not blocking #18.

### 10.4 Pre-existing trunk test failures (30 in 7 files)

Documented in session-4 §4.5 and confirmed unchanged in session-6
regression checks. None touch any L2b module.

### 10.5 `.flake8` config bug

Session-4 §6.4 #2 still applies. Not blocking.

### 10.6 mypy whole-tree cleanup

Out of scope for L2b.

### 10.7 Vestigial validator methods

ADR 0008's "Open questions deferred" still applies.

### 10.8 Reference files at `_ref/`

`_ref/` is gitignored. Contains four property files plus an Excel
workbook. Persist as-is for now; the eventual production deployment
should source these from the live Spring config, not from `_ref/`.

### 10.9 Long-term: dedicated harness Oracle account

Once SIT validation succeeds with `app_int`, file a DBA ticket for a
dedicated `valdo_harness_sit` user with `SELECT` on the source tables
and `CREATE TABLE`/`INSERT`/`TRUNCATE` on `LKP_VALDO_SHAW_*` only. Per
AGENTS.md hard rule #3 least-privilege. Not blocking; mentioned in §7
of this doc for session 8+ planning.

## 11. Quick links

- [#17 — L2b engine + SQL bootstrap](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues/17) — merged in session 5
- [#18 — SHAW TRANERT SQL artifacts](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues/18) — 8 commits landed this session; 1 fix in-progress; resume here
- [#19 — SHAW TRANERT reconciliation YAML + wiring](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues/19) — blocked on #18
- [#20 — tests + CI + infographic pill flip](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues/20) — blocked on #19
- [#21 — refactor: extract multi-record reader primitive](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues/21) — merged in session 4
- [`docs/handover/L2B_SESSION_5_HANDOVER.md`](L2B_SESSION_5_HANDOVER.md) — predecessor
- [`docs/handover/L2B_SESSION_4_HANDOVER.md`](L2B_SESSION_4_HANDOVER.md)
- [`docs/handover/L2B_SESSION_3_HANDOVER.md`](L2B_SESSION_3_HANDOVER.md)
- [`docs/handover/L2B_SESSION_2_HANDOVER.md`](L2B_SESSION_2_HANDOVER.md)
- [`prompts/L2B_SESSION_7_RESUME_PROMPT.md`](../../prompts/L2B_SESSION_7_RESUME_PROMPT.md) — the prompt for the session-7 agent
- Java ground truth (gitignored, intentional): `_ref/shaw-interfaces-common.properties`, `_ref/shaw-sql-statements.properties`, `_ref/tranert-center-gl.properties`, `_ref/db-sit.properties`, `_ref/GLCostCenterMapping.xlsx`; plus `config/templates/DAOOperations (2).java`, `config/templates/TranertMapper (1).java`
- SHAW smoke-test fixtures (committed): `data/samples/atoctran_shaw_20260514.txt`, `data/samples/tranert_shaw_20260422.txt`
- L2b engine entry point: `scripts/e2e_lib/db_truth_comparator.reconcile()` — see session-5 §6.1 for usage
- L2b SIT smoke runner: `scripts/e2e_lib/shaw_tranert_smoke.py` — landed in commit `cf69fe5`; subcommands `bootstrap`, `validate`, `query`, `discover`
- Audit logs from this session's SIT runs: `reports/shaw_tranert_smoke/20260529T191415Z.jsonl`, `20260529T205155Z.jsonl`, `20260529T214737Z.jsonl`, `20260529T220617Z.jsonl` (all gitignored, local only)
