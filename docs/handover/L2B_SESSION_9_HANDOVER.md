# L2b SQL-Truth Gate — Session 9 Handover

**Generated:** 2026-06-01
**Predecessor:** [`docs/handover/L2B_SESSION_8_HANDOVER.md`](L2B_SESSION_8_HANDOVER.md) — read first; this doc is a *delta* on session 8.
**Branch at handover:** `feature/issue-18-shaw-tranert-sql` (head `4c8888d`, **local-only, never pushed**).
**Issues this session touched:** #18 (commits 5b2a, 5b2b, and 6 landed: the complex per-record-type expected tables + the SQL parse-smoke test).

---

## 1. Headline outcome

**The per-record-type expected-table set is now COMPLETE, and the SQL
parse-smoke test (commit 6) is committed.** Session 9 landed the two
"complex" milestones plus the standing parse gate:

| Commit | Title |
|---|---|
| `faf0920` | `feat(sql): materialize per-contact SHAW TRANERT 32005 expected table (5b2a)` |
| `6e2d65e` | `feat(sql): materialize SHAW TRANERT 32010 + 32025 expected tables (5b2b)` |
| `4c8888d` | `test(e2e): add SHAW TRANERT SQL parse-smoke test (commit 6)` |

All seven per-record-type expected objects now exist as app_int-owned CTAS
tables, materialized + refreshed by the same shape-(B) pattern as 5b1:

| Table | Cardinality | Rows (batch 2026-05-28) |
|---|---|---|
| `EXPECTED_BATCH_HEADER_TBL` | single row | 1 (`ITM_CNT_BRT` = 175) |
| `EXPECTED_32000_TBL` | one_per_driver_row | 28 |
| `EXPECTED_32005_TBL` | many_per_driver_row | 35 (per contact) |
| `EXPECTED_32010_TBL` | zero_or_one_per_driver_row | 28 |
| `EXPECTED_32025_TBL` | one_per_driver_row | 28 |
| `EXPECTED_32040_TBL` | one_per_driver_row | 28 |
| `EXPECTED_32075_TBL` | one_per_driver_row | 28 |

`ITM_CNT_BRT = 175 = 28 + 35 + 28 + 28 + 28 + 28` (correction #4: every
emitted detail row, including each 32005 contact row, counts).

### Session-start decisions (confirmed with the user)

1. **5b2 split, 32005 FIRST** (user choice), then 32010 + 32025 together.
2. **32005 deferred fields = option A** (ship now minus the ref-num-dependent
   fields, flag `regression_only`). See §3.
3. **32010 `ST_COD_ORI` = leg A only** (BK property lookup); the leg-B
   `stateProvinceMap` fallback is `regression_only` (correction #13).
4. **`LKP_VALDO_SHAW_*` vs `LKP_VALDO_TRANERT_*` (file- vs source-scoped)
   = option I**: keep 5b2 on the existing SHAW-scoped lookups; the
   file-common generalization is a SEPARATE ADR + issue once cross-source
   commonality is confirmed (the user raised "6 files but >12 sources" —
   real, but deferred, and a *correctness* question, not just naming, since
   we have no second source's TRANERT spec to compare yet). Do NOT bundle
   this into the harness work.

---

## 2. The batch date everything was validated against

**Batch date = 2026-05-28.** The driver (`V_SHAW_TRANERT_DRIVER`) resolves to
28 accounts on `TRUNC(BATCH_DATE) = 2026-05-28`. Note this is NOT the max
`BATCH_DATE_LOCATOR` date (2026-05-31) — the driver predicate
`CHG_OFF_CD = '1' AND TRUNC(M_DATE_PAID_OFF) = TRUNC(BATCH_DATE)` selects the
most recent batch where charge-off accounts paid off on their batch date.

**No batch date is pinned in any SQL.** The driver self-selects from whatever
is currently loaded in `app_int.SHAW_LOAN_MASTER` / `BATCH_DATE_LOCATOR`. All
row counts in this doc are for the 2026-05-28 load; they will change as SIT
data changes. This is by design (the harness re-derives each run).

---

## 3. Deferred fields — `regression_only` for the #19 YAML (DO NOT re-derive)

These are NOT bugs; they are deliberate, user-approved deferrals. Each is
marked with a `TODO(valdo-gap)` in `030_expected_tables.sql` and MUST be
declared `regression_only: true` in the issue #19 reconciliation YAML so L2b
does not compare them (L3 baseline diff covers them):

| Record | Field | Why deferred |
|---|---|---|
| 32005 | `CIF_REF_NUM_CUS` (omitted, no column) | Needs per-account contact ordinal `cifRefNum`; `V_SHAW_TRANERT_CONTACTS_MERGED` exposes no ref-num. Java assigns it in the caller's contact-iteration loop (stateful). |
| 32005 | `CIF_ACT_COD_CUS` 997/'P' short-circuit | The `cifRefNum == 997 && !primary_ref_flag -> 'P'` rule (Java L116-117) needs the same missing ordinal + primary flag. `CIF_ACT_COD_CUS` is emitted from the name-relation lookup ONLY. |
| 32010 | `ST_COD_ORI` leg B | `DAOOperations.stateProvinceMap` is cross-run JVM state (correction #13); L2b cannot reproduce it. Leg A (BK property lookup) IS emitted. |
| 32025 | `LN_OFC_CUR_COD` (omitted, no column) | `l.getMOfficer()` (correction #9) has no `M_OFFICER` column in `SHAW_LOAN_MASTER` (verified via SIT `ALL_TAB_COLUMNS`). |

**A future MR may extend `V_SHAW_TRANERT_CONTACTS_MERGED`** with
`CIF_REF_NUM` + `PRIMARY_REF_FLAG` ordinals to lift the two 32005 deferrals —
but only after the Java caller's contact-iteration order is confirmed against
`DAOOperations`. Do NOT guess that order.

---

## 4. Fan-out: the §3-of-session-8 lesson held (and how)

Every helper table that is not keyed one-row-per-account was deduped to the
grain the Java map/scalar lookup implies, so no expected table fanned out:

- **32005** (per-contact, the intended grain): joins `CONTACTS_MERGED`
  directly (already deduped to `(ACCT_NUM, CONTACT_ID)` by the helper). The
  CBRS-summary and LOANS_NAME joins are deduped to one row per account
  (`ROW_NUMBER() ... rn = 1`), because the Java reads both as single map
  values per account. Result: **35 rows = exactly the 35 non-skipped
  contacts** (28 rel 'A' + 7 rel 'B'); key `(ACCT_NUM, CONTACT_ID)` unique.
- **32010 / 32025**: the CBRS `dateClosed`, the BASE-segment contact
  termsFreq override, and the two ledger-variant lookups are all deduped to
  one row per account. Result: **28 each**, 28 distinct accounts, no fan-out.

**Lesson for any future expected table:** always `SELECT COUNT(*)` and
`COUNT(DISTINCT LN_NUM_ERT)` against the new table after building it and
compare to the driver (28). 32005 is the only intentional > 28 (per-contact).

---

## 5. Live SIT verification (proof, batch 2026-05-28, as app_int)

5b2a:
- bootstrap clean/idempotent (`reports/shaw_tranert_smoke/20260601T194436Z.jsonl`);
- validate 15/15 OK (`...194645Z.jsonl`);
- 32005 = 35 rows, key unique; header `ITM_CNT_BRT` = 119 (pre-5b2b).

5b2b:
- bootstrap clean/idempotent (`...213130Z.jsonl`);
- validate 17/17 OK (`...213324Z.jsonl`);
- 32010 = 28 (OGL_NTE_DAT_ORI non-null for all 28; portfolio-type 'C' +
  HGH_BAL_ORI non-null for the 4 typeX=8 rows; LN_TYP_ORI resolved to real
  ledger ints; ST_COD_ORI leg-A non-null for 28);
- 32025 = 28 (LGL_STA_COD_COD='STL' for 2, null for 26 — chgOffCd='1' is not
  in the R/P/N/B/J/F/X map, faithful to Java; RPO_COD_COD=0 for all 28);
- header `ITM_CNT_BRT` = 175.

Offline gates (all sessions): black clean; mypy clean
(`--follow-imports=silent --explicit-package-bases`); flake8 clean via
`--isolated` (the repo `.flake8` is still broken — session-7 §8 loose end #1,
unchanged). 232 unit tests pass across the L2b modules + the new 38-case
parse-smoke file.

---

## 6. Commit 6 — the parse-smoke test (NEW this session)

`tests/unit/test_e2e_shaw_tranert_sql_smoke.py` walks every `.sql` under the
tranert tree (18 files: 7 bootstrap/load + 11 query) and asserts:

- **Offline (always):** `_split_file` parses each into >= 1 non-blank
  statement, and every statement classifies `is_safe` (parametrized per file;
  includes a non-empty-tree guard so a glob regression can't pass vacuously).
- **Live (gated):** reuses `run_validate(as_app_int=True)` to confirm every
  CTAS body compiles on SIT. `_sit_connection_or_skip()` attempts a real
  connection and `pytest.skip`s on any failure, so the suite stays green
  without SIT. Marked `@pytest.mark.integration` (registered in `pytest.ini`).

Note: `cost2.sql` is a `SELECT` with bind variables (`:batch_date`,
`:rank_offset`); the classifier accepts it (binds are only refused for
`CREATE VIEW`, ORA-01027). The orphaned `020_expected_views.sql` still parses
+ classifies safe (its `CREATE VIEW` statements are whitelist-accepted) even
though app_int cannot execute them — see §8.

---

## 7. Verify state before doing anything (session 10)

```powershell
git log --oneline -n 5
# Expect:
#   4c8888d test(e2e): add SHAW TRANERT SQL parse-smoke test (commit 6)
#   6e2d65e feat(sql): materialize SHAW TRANERT 32010 + 32025 expected tables (5b2b)
#   faf0920 feat(sql): materialize per-contact SHAW TRANERT 32005 expected table (5b2a)
#   4368fd2 docs: add L2b session 8 handover (5b1 simple expected tables)
#   8527b33 feat(sql): materialize simple SHAW TRANERT expected tables (5b1)

git status --short
# Expect exactly two unstaged M files (still left for reference, unchanged
# since session 6):
#   M config/e2e/sources/SHAW/sql/tranert/00_bootstrap/020_expected_views.sql
#   M config/e2e/sources/SHAW/sql/tranert/20_query/cost2.sql
# Plus untracked leavings:
#   ?? "config/templates/DAOOperations (2).java"
#   ?? "config/templates/TranertMapper (1).java"
#   ?? prompts/L2B_Session_7_Continue_prompt.txt
#   (this handover will also appear once written)

git stash list
# Expect: stash@{0} unchanged from sessions 2-8

python -m pytest tests/unit/test_e2e_shaw_tranert_smoke.py tests/unit/test_e2e_shaw_tranert_sql_smoke.py --no-cov -q --tb=line
# Expect: 84 + 38 = 122 passed (the live integration test runs and passes
# when SIT is reachable; it SKIPS otherwise, lowering the count by 1).
```

If any of these are off, **STOP** and reconcile before proceeding.

### Windows shell gotchas (carried forward, re-confirmed session 9)

- **`run_command` runs in a shell where `;` chaining and `Remove-Item` FAIL.**
  Use `del a b c`; run each `git` command in its own call; do NOT chain `;`.
  `python -c "..."` single-line is OK. **`head`/`tail` are NOT available** —
  do slicing in Python instead.
- **`read_files` does NOT satisfy `edit_file`'s read requirement.** Call
  `read_file` on a path before `edit_file` will touch it.
- **Clean up `_*.py` / `_*.txt` scratch files BEFORE committing** — and note
  a `del` issued just before a user interjection may not have executed; always
  re-check `git status --short` for stray `_*` files before committing.
- **`.flake8` still broken.** Lint via
  `python -m flake8 --isolated --max-line-length=100 --extend-ignore=E203,W503 <files>`.
- **mypy:** `python -m mypy --follow-imports=silent --explicit-package-bases <file>`.
- **gitignored paths** (`config/templates/*.java`, `_ref/*`,
  `config/e2e/sources/SHAW/lookups/`): read via `run_command` + Python
  `Path.read_text`, not the file tools.
- **Multi-paragraph commit messages:** write `_commit_msg_tmp.txt`, then
  `git commit -F`, then `del`.
- **Column / data verification against SIT:** the `discover` subcommand only
  checks object OWNERSHIP (ALL_OBJECTS). For COLUMN-level checks, write a
  short throwaway Python that imports `_connect_sit` and queries
  `ALL_TAB_COLUMNS` read-only, then delete it. (Used heavily this session to
  ground every 5b2 field before authoring.)
- **Live smoke runner CLI:** global flags first —
  `python -m scripts.e2e_lib.shaw_tranert_smoke --as-app_int bootstrap`
  (NOT `bootstrap --as-app_int`).

---

## 8. Next plan (session 10) — in priority order

Issue #18's authoring milestones (5a/5b1/5b2a/5b2b/6) are **DONE**. What
remains for #18 and the loose ends:

### 8.1 Decide whether #18 is complete enough to open the MR

Re-read issue #18's acceptance criteria. The expected-table SQL, the
extractor, the lookups, the smoke runner, and the parse-smoke test all exist.
The reconciliation YAML + `run_source.py` integration are explicitly **issue
#19**, not #18. If the user agrees #18's scope is met, the next action is to
**push the branch and open the MR** titled
`feat: add SHAW TRANERT SQL artifacts for L2b SQL-truth gate` — but ONLY on
explicit user confirmation (pushing + MR creation are stop-and-ask, §9).

### 8.2 Resolve the two orphaned working-tree M files

`020_expected_views.sql` and `20_query/cost2.sql` have carried uncommitted
edits since session 6. `020_expected_views.sql` is the pre-shape-(B) CREATE
VIEW file, superseded by `030_expected_tables.sql` (app_int lacks CREATE VIEW).
Decide with the user: commit the edits, revert them, or delete
`020_expected_views.sql` outright (the parse-smoke test would then cover one
fewer file). Do not silently leave them dangling into the MR.

### 8.3 Carried-forward optional / later items (unchanged)

- `.flake8` `chore:` fix (session-7 §8 #1).
- 3 stray `uzapp_ad0.V_SHAW_TRANERT_*` views (harmless; leave or clean).
- Dedicated least-privilege `valdo_harness_sit` user (replacing app_int).
- **The `LKP_VALDO_SHAW_*` -> `LKP_VALDO_TRANERT_*` file-vs-source
  generalization** (user's "6 files, >12 sources" point): needs an ADR + a
  second source's TRANERT spec to confirm the dept->ledger / act-typ mappings
  are genuinely file-common before promoting. Session-9 decision was option I
  (defer). The `LKP_VALDO_TRANERT_SOURCE_REGISTRY` dimension (session 7) is
  the existing seam.

---

## 9. Stop-and-ask rules (carried forward, unchanged)

- Touching `stash@{0}`.
- Modifying anything under `src/` (AGENTS.md hard rule #1; the carve-out does
  NOT apply to #18 — scripts + config only).
- Creating any lookup/expected content you would have to **invent** — the
  13-correction list on #17 and the Java in `config/templates/` are the only
  authorities for row logic. In particular do NOT guess the 32005
  contact-iteration ordinal/primary-flag order needed to lift the deferred
  ref-num fields.
- Pushing the branch to origin, force-pushing, or opening an MR.
- Creating any new issues.
- Running anything destructive against SIT (DROP/DELETE/UPDATE). The whitelist
  refuses these by design.

---

## 10. Reading list for session 10 (priority order)

1. **This document** (`L2B_SESSION_9_HANDOVER.md`).
2. `docs/handover/L2B_SESSION_8_HANDOVER.md` — predecessor (5b1 pattern, the
   fan-out lesson, the CLI gotcha).
3. **AGENTS.md** — hard rules (#1 no `src/`, #3 no secrets, #6 stop at
   milestones).
4. **Issue #18** acceptance criteria + the **13-correction list on #17** —
   authoritative for any remaining row logic and for the #19 YAML
   `regression_only` flags (§3).
5. `config/e2e/sources/SHAW/sql/tranert/00_bootstrap/030_expected_tables.sql`
   + `10_load/020_refresh_expected.sql` — the complete expected-table set
   (read the `TODO(valdo-gap)` markers for the deferred fields).
6. `tests/unit/test_e2e_shaw_tranert_sql_smoke.py` — the new parse gate.
7. `scripts/e2e_lib/shaw_tranert_smoke.py` — the classifier/allow-list/runner.
8. Java ground truth in `config/templates/` + `_ref/` (gitignored; read via
   Python).
