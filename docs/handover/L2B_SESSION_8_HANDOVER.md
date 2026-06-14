# L2b SQL-Truth Gate — Session 8 Handover

**Generated:** 2026-06-01
**Predecessor:** [`docs/handover/L2B_SESSION_7_HANDOVER.md`](L2B_SESSION_7_HANDOVER.md) — read first; this doc is a *delta* on session 7.
**Branch at handover:** `feature/issue-18-shaw-tranert-sql` (head `8527b33`, **local-only, never pushed**).
**Issues this session touched:** #18 (commit 5b1 landed: the four simple `EXPECTED_*_TBL` per-record-type tables).

---

## 1. Headline outcome

**Commit 5b1 is done, committed, and live-validated against SIT.** The four
"simple" (cardinality `one_per_driver_row`) per-record-type expected-result
tables now materialize as app_int-owned CTAS tables composing the session-7
helper tables:

| Table | Cardinality | Notes |
|---|---|---|
| `EXPECTED_32000_TBL` | one_per_driver_row | Pure direct copy + constants |
| `EXPECTED_32040_TBL` | one_per_driver_row | Three-branch `ACT_TYP_CBRS` cascade + `DAT_DLQ_STR_CBRS` per-field predicate + `PRE_COF_L1`/`PET_DAT`/`LAS_*` |
| `EXPECTED_32075_TBL` | one_per_driver_row | `RCF_DUE_REC` from `V_SHAW_TRANERT_COST_MERGED` |
| `EXPECTED_BATCH_HEADER_TBL` | single row | Constants + `ITM_CNT_BRT` = sum of materialized detail counts |

One commit landed this session (local-only):

| Commit | Title |
|---|---|
| `8527b33` | `feat(sql): materialize simple SHAW TRANERT expected tables (5b1)` |

**Decision confirmed at session start (per session-7 §10.2):** the 5b1/5b2
split stands, and **32040 stays in 5b1** (option 1). Although 32040's
`ACT_TYP_CBRS` is a three-branch CASE-cascade, it is structurally
`one_per_driver_row` and the lookups it needs (`LKP_VALDO_SHAW_LEDGER_CD_BY_DEPT`,
`LKP_VALDO_SHAW_ACT_TYP_UI`) already exist, so it was doable now.

**Header `ITM_CNT_BRT` policy confirmed (option 1):** the header carries the
**sum of the per-record-type `EXPECTED_*_TBL` counts that exist** (currently
32000 + 32040 + 32075). It is intentionally correct-for-what-is-materialized
and **self-extends** as 5b2 tables land. The authoritative whole-file
assertion (`header.ITM-CNT-BRT == sum(detail_row_counts)`) remains the
comparator's job via the reconciliation YAML (issue #19), not this table.

---

## 2. What landed — commit `8527b33` (5b1)

**SQL (4 new CTAS + 4 refresh blocks):**
- `00_bootstrap/030_expected_tables.sql` — four structure-only CTAS blocks
  appended after the 10 helper tables. Same idempotent pattern as the
  helpers: `q'[ ]'` quoting, `SELECT * FROM ( <body> ) WHERE 1 = 0`,
  ORA-00955 trap. Every constant column is `CAST` to an explicit type/width.
- `10_load/020_refresh_expected.sql` — four `TRUNCATE` + `INSERT … SELECT`
  blocks. **The header is refreshed LAST** so its `ITM_CNT_BRT` count sees
  the already-populated detail tables.

**Classifier / allow-list (`scripts/e2e_lib/shaw_tranert_smoke.py`):**
- Added the four `APP_INT.EXPECTED_*_TBL` names to `_TRUNCATE_ALLOWLIST`,
  replacing the session-7 NOTE placeholder. **No other code change** — the
  `EXPECTED_` table prefix was already classifier-accepted, and `run_validate`
  auto-discovers CTAS table names by regex-scanning `030_expected_tables.sql`,
  so the four new tables wired themselves into validation.

**Test (`tests/unit/test_e2e_shaw_tranert_smoke.py`):**
- The session-7 placeholder guard
  `test_expected_prefix_not_yet_allowlisted_is_refused` was split into:
  - `test_expected_5b1_tables_truncate_allowed` (the four 5b1 tables now allowed), and
  - `test_expected_5b2_prefix_not_yet_allowlisted_is_refused` (`EXPECTED_32005_TBL` still refused).
- The drift test `test_truncate_allowlist_matches_bootstrap_sql` keeps the
  allow-list and the bootstrap `CREATE TABLE` set identical — CI fails on drift.

**Row logic provenance (do not re-derive; it's from ground truth):**
- `TranertMapper (1).java`: `getTranertCus32000` (L69-86), `getTranertCus32040`
  (L541-652), `getTranertCus32075` (L655-678), `getTranertHeader` (L32-67).
- `DAOOperations (2).java`: the `LoanMasterData` result-set column names
  (`ACCT_NUM`, `BATCH_DATE`, `DTE_LAST_RUN`, `M_DATE_PAID_OFF`,
  `M_CHARGE_OFF_AMT`, `M_FIRST_DELQ_DT`, `M_TRW_COMMENTS`,
  `M_ACB_COMP_COND_CD`, `M_BANKRUPTCY_DT`, `BK`, `DEPT`, `M_MISC_CODE2`,
  `M_PAYOFF_TRANS`); `ACCOUNT_TYPE` from `CBRS_TRW_SUMMARY` (L3724).
- The 13-correction list on issue #17 (esp. #4 header count, #10/#11 the
  32040 `ACT_TYP_CBRS` cascade + `DAT_DLQ_STR_CBRS` predicate).

---

## 3. Live-surfaced bug fixed this session (read before 5b2)

These CTAS/refresh bodies had never run on Oracle. Live validation surfaced
**one real fan-out bug** that 5b2 must not repeat:

- **32040 → CBRS join fanned 28 driver rows out to 246.**
  `V_SHAW_TRANERT_CBRS_ACCOUNT_SUMMARY` holds **~10 rows per account**
  (they differ only in `ACCOUNT_STATUS`/`PAST_DUE`, which 32040 does *not*
  read). The Java reads CBRS as a **`Map<acctNum, CBRSSummaryData>`**
  (`cbrAccountMap.get(acctNum)` — one value per account), so a naive
  `LEFT JOIN` to the raw summary is wrong.
  **Fix:** join to a per-account-deduped projection of the only column 32040
  consumes:
  ```sql
  LEFT JOIN (SELECT DISTINCT ACCOUNT_NUMBER, ACCOUNT_TYPE
               FROM app_int.V_SHAW_TRANERT_CBRS_ACCOUNT_SUMMARY) cbrs
         ON cbrs.ACCOUNT_NUMBER = d.ACCT_NUM
  ```
  Verified safe: `ACCOUNT_TYPE` is **constant within an account**
  (0 accounts have >1 distinct `ACCOUNT_TYPE`), so the dedup changes no value.

> **Lesson for 5b2:** any helper table that is *not* keyed one-row-per-account
> (CBRS summary is the obvious one; also watch `CONTACTS_MERGED`, `BK1`/`BK3`,
> `STATE_PROVINCE`, `LOANS_NAME`) will fan out a `one_per_driver_row` or
> `zero_or_one_per_driver_row` expected table unless you dedup/aggregate to
> the grain the Java map/scalar lookup implies. **Always check row counts
> against `V_SHAW_TRANERT_DRIVER` (28) after building each 5b2 table.**
> 32005 is the deliberate exception — it is `many_per_driver_row`
> (per-contact, composite key `(ACCT_NUM, CONTACT_ID)`).

---

## 4. Live SIT verification (proof)

All as `app_int` via `--as-app_int` (creds come from `.env`, loaded by
`SecretResolver` — they are NOT in the process environment, so a bare
`os.environ` check shows them "missing"; that is expected):

- `bootstrap` → **6 files applied, 0 failures, idempotent** (re-ran clean).
- `validate` → **14/14 OK** (10 helpers + 4 new `EXPECTED_*_TBL`).
- Row counts: `V_SHAW_TRANERT_DRIVER` = 28; `EXPECTED_32000/32040/32075_TBL`
  = **28 each**; `EXPECTED_BATCH_HEADER_TBL` = 1 row, `ITM_CNT_BRT` = **84**
  (= 28 × 3); 32040 `ACT_TYP_CBRS` non-null for all 28.

**Offline gates:** 84 unit tests pass in
`tests/unit/test_e2e_shaw_tranert_smoke.py` (was 83; net +1 from the
guard-test split). black clean; mypy clean; flake8 clean **via `--isolated`**
(the `.flake8` is still broken — session-7 §8 loose end #1, unchanged).
The bootstrap splitter parses every new CTAS + refresh statement as `is_safe`.

---

## 5. CLI invocation gotcha (new this session)

The `shaw_tranert_smoke` global flags (`-v`, `--as-app_int`) are **top-level**
args and must come **before** the subcommand:

```powershell
# CORRECT:
python -m scripts.e2e_lib.shaw_tranert_smoke --as-app_int bootstrap
python -m scripts.e2e_lib.shaw_tranert_smoke --as-app_int validate
# WRONG (argparse: "unrecognized arguments: --as-app_int"):
python -m scripts.e2e_lib.shaw_tranert_smoke bootstrap --as-app_int
```

Subcommands: `bootstrap`, `validate`, `query <name> [--limit N]`,
`discover <names...>`.

---

## 6. Bootstrap / load order (current — unchanged file list, expanded contents)

```
00_bootstrap/010_lookup_tables.sql       7 PL/SQL CREATE-TABLE (LKP_VALDO_SHAW_*)
00_bootstrap/040_source_registry.sql     1 PL/SQL CREATE-TABLE (LKP_VALDO_TRANERT_SOURCE_REGISTRY)
10_load/010_load_lookups_from_csv.sql    168 TRUNCATE/INSERT VALUES (auto-generated)
10_load/030_refresh_source_registry.sql  1 TRUNCATE + 18 INSERT VALUES
00_bootstrap/030_expected_tables.sql     14 PL/SQL CTAS  (10 helpers + 4 EXPECTED_*_TBL)  <-- +4 this session
10_load/020_refresh_expected.sql         28 TRUNCATE + 28 INSERT…SELECT (14 tables × 2)   <-- +8 this session
```

`run_validate` now probes **14** CTAS table names from `030_expected_tables.sql`.

---

## 7. Verify state before doing anything (session 9)

```powershell
git log --oneline -n 5
# Expect:
#   8527b33 feat(sql): materialize simple SHAW TRANERT expected tables (5b1)
#   1899de5 docs: add L2b session 7 handover (shape B + cross-source registry)
#   cbf39c4 feat(sql): add cross-source TRANERT source registry dimension
#   30be23d fix(sql): materialize SHAW TRANERT helper result sets as app_int tables (shape B)
#   0f02182 docs: add L2b session 6 handover and session 7 resume prompt

git status --short
# Expect exactly two unstaged M files (still left for 5b reference):
#   M config/e2e/sources/SHAW/sql/tranert/00_bootstrap/020_expected_views.sql
#   M config/e2e/sources/SHAW/sql/tranert/20_query/cost2.sql
# Plus untracked leavings:
#   ?? "config/templates/DAOOperations (2).java"
#   ?? "config/templates/TranertMapper (1).java"
#   ?? prompts/L2B_Session_7_Continue_prompt.txt
#   (this handover will also appear once written)

git stash list
# Expect: stash@{0} unchanged from sessions 2-7

python -m pytest tests/unit/test_e2e_shaw_tranert_smoke.py --no-cov -q --tb=line
# Expect: 84 passed
```

If any of these are off, **STOP** and reconcile before proceeding.

### Windows shell gotchas (carried forward, re-confirmed session 8)

- **Despite the env hint claiming PowerShell, `run_command` runs in a shell
  where `;` chaining and `Remove-Item` FAIL.** Use `del a b c` (not
  `Remove-Item`); run each `git` command in its own call; do NOT chain with
  `;`. `python -c "..."` single-line is OK.
- **`read_files` does NOT satisfy `edit_file`'s read requirement.** You must
  call `read_file` on a path before `edit_file` will touch it (even if you
  already read it via `read_files`).
- **`.flake8` still broken** (§8 #1, session 7). Lint via
  `python -m flake8 --isolated --max-line-length=100 --extend-ignore=E203,W503 <files>`.
- **mypy:** `python -m mypy --follow-imports=silent --explicit-package-bases <file>`.
- **gitignored paths** (`config/templates/*.java`, `_ref/*`): read via
  `run_command` + Python `Path.read_text`, not the file tools.
- **Clean up `_*.py` / `_*.txt` scratch files** before committing.
- **Multi-paragraph commit messages:** write `_commit_msg_tmp.txt`, then
  `git commit -F`, then `del`.

---

## 8. Next plan (session 9) — in priority order

### 8.1 Commit 5b2 — complex `EXPECTED_*_TBL` tables (START HERE)

The harder record types with CASE-cascade / bankruptcy / contact-merge logic:
`EXPECTED_32005_TBL`, `EXPECTED_32010_TBL`, `EXPECTED_32025_TBL`. These lean
on `CONTACTS_MERGED`, `BK1`/`BK3`, `STATE_PROVINCE`, `LOANS_NAME`, and the
remaining `LKP_VALDO_SHAW_*` lookups.

**Authoritative sources (read first, same as 5b1):**
- The **13-correction list on issue #17** — esp. items #1 (32010 conditional
  suppression, `zero_or_one_per_driver_row`, predicate `CHG_OFF_CD = '1'`),
  #2 (32005 per-contact `many_per_driver_row`, key `(ACCT_NUM, CONTACT_ID)`,
  skip `nameRelationship ∈ {M,N,null}`), #5-#9 (32010 field CASEs),
  #12 (32005 `CIF-CSM-INF-IND-CUS`/`ECOA-CODE-CUS` multi-source CASE chains),
  #13 (`stateProvinceMap` cross-run state → flag dependent fields
  `regression_only: true`; L2b cannot reproduce stateful Java).
- `config/templates/TranertMapper (1).java`: `getTranertCus32005` (L89-257),
  `getTranertCus32010` (L258-435), `getTranertCus32025` (L436-540), and the
  two private helpers `extractedDismissedAndDischargeDateIsNull` (L680) /
  `extractedDismissedIsNullAndDischargeDateIsNotNull` (L690).
- `config/templates/DAOOperations (2).java` result-set reads (gitignored).
- `_ref/shaw-sql-statements.properties` (gitignored).

**Per new `EXPECTED_*_TBL` (same recipe as 5b1):**
1. Add the CTAS to `030_expected_tables.sql` (structure-only, `q'[ ]'`,
   ORA-00955 trap, `WHERE 1 = 0`).
2. Add the `TRUNCATE` + `INSERT…SELECT` to `020_refresh_expected.sql`.
   **Keep the header refresh LAST** and **extend its `ITM_CNT_BRT` sum** with
   a `COUNT(*)` term for each new `one_per_driver_row`/`zero_or_one` table.
   (Decide whether 32005 — `many_per_driver_row` — counts toward
   `ITM_CNT_BRT`: per correction #4 the header counts **total detail rows**,
   so **yes, every emitted detail row including each 32005 contact row
   counts**. Add `(SELECT COUNT(*) FROM …EXPECTED_32005_TBL)` too.)
3. Add `APP_INT.EXPECTED_*_TBL` to `_TRUNCATE_ALLOWLIST` (drift test enforces).
4. **Check row counts vs the driver (28)** — see §3. 32010 should be
   ≤ 28 (suppressed when `CHG_OFF_CD <> '1'`; though note the *driver itself*
   is already `CHG_OFF_CD='1'`, so confirm the real predicate grain);
   32025 should be 28; 32005 will be > 28 (per-contact).
5. Validate live as app_int (`--as-app_int bootstrap` then `--as-app_int validate`).

**Stop-and-ask** (carried from session-7 §11) before inventing any row logic:
the 13-correction list and the Java are the only authorities. In particular
do not guess the 32005 contact-skip set, the bk1/bk3 chapter-map letters, or
which fields are `regression_only`.

### 8.2 Commit 6 — parse-smoke test (§10.3 from session 7)

New `tests/unit/test_e2e_shaw_tranert_sql_smoke.py` that walks every `.sql`
under `config/e2e/sources/SHAW/sql/tranert/` and asserts:
- (offline) `sql_bootstrap._split_statements` parses every file and every
  statement classifies `is_safe`;
- (live, when SIT reachable) every helper/expected table body compiles —
  reuse the `validate` subcommand.

(The offline half of this is already effectively passing — it was run ad-hoc
this session — but it is not yet a committed test.)

### 8.3 Optional / later (unchanged from session 7)

- The `.flake8` `chore:` fix (§8 #1).
- 3 stray `uzapp_ad0.V_SHAW_TRANERT_*` views (harmless; leave or clean).
- Dedicated least-privilege `valdo_harness_sit` user (session 9+).
- Generalize `LKP_VALDO_SHAW_*` → `LKP_VALDO_TRANERT_*` (defer until a 2nd
  source arrives + an ADR).
- Consider joining `LKP_VALDO_TRANERT_SOURCE_REGISTRY` into helpers — only if
  5b2 needs it; don't add speculatively.

---

## 9. Stop-and-ask rules (carried forward, unchanged)

- Touching `stash@{0}`.
- Modifying anything under `src/` (AGENTS.md hard rule #1; the carve-out does
  not apply to #18 — this is scripts + config only).
- Creating any lookup/expected content you would have to **invent** — the
  13-correction list and the Java are the only authorities for row logic.
- Force-pushing, or pushing the branch to origin.
- Running anything destructive against SIT (DROP/DELETE/UPDATE). The
  whitelist refuses these by design.

---

## 10. Reading list for session 9 (priority order)

1. **This document** (`L2B_SESSION_8_HANDOVER.md`).
2. `docs/handover/L2B_SESSION_7_HANDOVER.md` — predecessor (shape B, the
   helper tables, the cross-source registry, the app_int privilege findings).
3. **AGENTS.md** — hard rules (#1 no `src/`, #3 no secrets, #6 stop at
   milestones).
4. **Issue #18** + the **13-correction list on #17** — authoritative for
   5b2 expected-row logic.
5. `config/e2e/sources/SHAW/sql/tranert/00_bootstrap/030_expected_tables.sql`
   + `10_load/020_refresh_expected.sql` — the 5b1 pattern 5b2 extends
   (esp. the 32040 deduped-CBRS join, §3).
6. `config/e2e/sources/SHAW/sql/tranert/00_bootstrap/020_expected_views.sql`
   — orphaned but holds canonical helper SQL bodies for reference.
7. `scripts/e2e_lib/shaw_tranert_smoke.py` — the classifier/allow-list/runner.
8. Java ground truth in `config/templates/` + `_ref/` (gitignored; read via
   Python).
