# L2b SQL-Truth Gate — Session 10 Handover

**Generated:** 2026-06-02
**Predecessor:** [`docs/handover/L2B_SESSION_9_HANDOVER.md`](L2B_SESSION_9_HANDOVER.md) — read first; this doc is a *delta* on session 9.
**Branch / trunk:** `feature/issue-11-kill-file-search` (this repo's trunk, **not** `main`), head `9f849b9`, **pushed to origin**. Surfaced on MR [!3](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/merge_requests/3).
**Issues this session touched:** #18 (closed-out scope, merged), #19 (wiring landed), #22 (new — gate green), infographic doc.

---

## 1. Headline outcome

**The L2b SQL-Truth gate now passes end-to-end on SIT for SHAW TRANERT, with
zero false positives**, and the whole #18→#19→#22 chain is merged to trunk.

Session 9 left the per-record-type expected tables built but the gate not yet
wired. Session 10:

1. Landed the two orphaned working-tree files from session-9 §8.2 (`4e74257`).
2. Wired the gate into the harness (#19, `520976d`).
3. Format-aligned the expected SQL and fixed a real cost double-count so the
   live reconcile went **392 violations → 0** (#22, `a0eb9cb`).
4. Added an "L2b SQL-Truth Gate" tab to the infographic (`9f849b9`).

| Commit | Title |
|---|---|
| `4e74257` | `fix(sql): inline cost2 as standalone query and schema-qualify base tables` |
| `520976d` | `feat: wire L2b SQL-truth gate for SHAW TRANERT` |
| `a0eb9cb` | `fix(sql): format-align L2b expected SQL + fix cost double-count for SHAW TRANERT` |
| `9f849b9` | `docs: add L2b SQL-Truth Gate tab to the Valdo infographic` |

### Live proof (SIT, batch 2026-05-28, as app_int)

- `reconcile()` against `_ref/tranert_shaw_20260529.txt`: **176 rows**, every
  record type dispatched, all keys matched (1/28/35/28/28/28/28),
  **0 violations**.
- Known-bad: corrupting one field (`LCT-COD-NEW1`) → exactly **1**
  `field_mismatch` → one `FailureRecord`.
- Offline: 264 e2e unit tests pass; parse-smoke green offline + live.

---

## 2. What got wired (issue #19)

All in `scripts/` + `config/` only (AGENTS.md hard rule #1: no `src/`).

- **Reconciliation spec** `config/e2e/sources/SHAW/reconciliation/tranert.yml`
  — 6 detail types + Batch Header. **Authored in the loader's REAL schema**
  (`fields:` list of `{file_field, expected_column[, predicate, regression_only]}`),
  NOT the `field_map:` shorthand shown in the issue description. The loader
  (`reconciliation_spec.py`) only accepts the `fields:` list form.
- **7 query wrappers** `20_query/expected_*.sql` — thin SELECTs over the
  materialized `app_int.EXPECTED_*_TBL` CTAS tables. (The issue's directory
  layout named `expected_*.sql` as *views*; they do not exist as views —
  app_int lacks `CREATE VIEW`, session-7 shape B.)
- **`run_source.py`**: orchestrator-driven `L2b_sql_truth` gate
  (`GATE_L2B_SQL_TRUTH`). Runs after the output-phase Valdo gates. Skips with
  `l2b_not_configured` when no YAML; opt-out `VALDO_E2E_DISABLE_L2B=1`; lazy
  Oracle connection; one capped (500) `FailureRecord` per violation.
- **`generate_pipeline_yaml.py`**: documents `L2b_sql_truth` as
  orchestrator-driven and **intentionally excluded** from the emitted pipeline
  YAML (`_ORCHESTRATOR_DRIVEN_GATES`), like the Java shell-outs. It is NOT a
  Valdo-runnable step.
- **`build_rollup_index.py`**: renders the `L2b` layer label.
- **`SHAW.yml`**: `L2b_sql_truth { blocking: true, invoke_java: false }`.
  **`SRC_A.yml`** + any other source: non-blocking (no YAML yet).

### Two engine constraints that drove the design (do NOT "simplify" away)

1. **`record_types` keys are the UMBRELLA names** (`rt_32000`, `rt_32005`,
   …, `batch_header`), NOT the bare codes. The multi-record reader yields the
   umbrella's logical name as `ParsedRow.record_type`, and the engine
   dispatches by that. The umbrella is `config/mappings/SHAW_TRANERT.yaml`.
2. **The spec `key` is matched on BOTH sides** — the parsed file row (dash
   field names, e.g. `LN-NUM-ERT`) and the expected rowset (underscore
   columns). So each `expected_*.sql` **aliases the key column to the dash
   form via an Oracle quoted identifier** (`... AS "LN-NUM-ERT"`). `key:` in
   the YAML uses the dash form. Non-key `expected_column` uses the underscore
   table-column names.

---

## 3. What made the gate green (issue #22)

The engine does **strict trimmed-string equality** (intentional, per #17), but
the expected SQL returned typed Oracle values while the file is fixed-width
text. First live run = 392 `field_mismatch`es. Two root causes:

### 3a. Format-alignment (387 of 392)

The 7 wrappers now project each reconciled column in the file's representation,
grounded in the per-record-type mapping JSON. Wrappers project an **explicit
column list (not `t.*`)** so a formatted alias never collides with a raw column.

| Field class | File form | SQL projection |
|---|---|---|
| dates (`MM/DD/CCYY`) | `05/28/2026` | `TO_CHAR(d, 'MM/DD/YYYY')` |
| `9(n)` ints | `000000175` | `LPAD(TO_CHAR(x), n, '0')` |
| `-Z(12).9(2)` amounts | `.00`, `660.00` | `LTRIM(TO_CHAR(x, 'FM999999999990.00'), '0')` |

The amount mask gives a leading `0`; `LTRIM(..,'0')` strips it so `0`→`.00`,
`0.5`→`.50`, `660`→`660.00`, `10`→`10.00`. **Verified against Oracle.**

### 3b. Two genuine discrepancies (grounded in the Java, not guessed)

1. **`ORG-LVL-NUM6-COD`** — was a **typo in the reconciliation YAML**. The COD
   mapping field is `ORG-LVL-NUM-6-COD` (dash before the 6). Fixed the
   `file_field`; `expected_column` stays `ORG_LVL_NUM6_COD` (the table column).
2. **`RCF-DUE-REC`** (10 of 28 accounts, SQL > file) — a **real cost
   double-count**. The SQL summed `UNPAID_LCHRGS` on every fee row; Java
   `DAOOperations.get32075Cost()` adds `unpaidLchrgs` only on the **first** row
   per account and `feeCurrBal` on every row. Fixed `cost1_agg` and
   `cost2_union` to **`MAX(UNPAID_LCHRGS) + SUM(FEE_CURR_BAL)`** in both
   `00_bootstrap/030_expected_tables.sql` (structure) and
   `10_load/020_refresh_expected.sql` (data).

---

## 4. Verify state before doing anything (session 11)

```powershell
git log --oneline -n 5
# Expect:
#   9f849b9 docs: add L2b SQL-Truth Gate tab to the Valdo infographic
#   a0eb9cb fix(sql): format-align L2b expected SQL + fix cost double-count ...
#   520976d feat: wire L2b SQL-truth gate for SHAW TRANERT
#   4e74257 fix(sql): inline cost2 as standalone query and schema-qualify ...
#   ac9de36 docs: add L2b session 9 handover ...

git status --short
# Expect ONLY the gitignored leavings (unchanged since session 7):
#   ?? "config/templates/DAOOperations (2).java"
#   ?? "config/templates/TranertMapper (1).java"
#   ?? prompts/L2B_Session_7_Continue_prompt.txt
#   (this handover will also appear once written)

# Offline gate (no SIT needed):
python -m pytest tests/unit/test_e2e_run_source.py tests/unit/test_e2e_reconciliation_spec.py tests/unit/test_e2e_db_truth_comparator.py tests/unit/test_e2e_shaw_tranert_sql_smoke.py --no-cov -q --tb=line
# Expect: all pass; the live integration parse-smoke SKIPS without SIT.
```

### Windows shell gotchas (carried forward, re-confirmed session 10)

- **CRLF files break `edit_file`.** `Valdo-Infographic.html` and several SQL
  files are CRLF. `edit_file` matches with LF and silently finds nothing. For
  CRLF files, write a throwaway Python script that does
  `txt = path.read_text(); txt = txt.replace(old, new); path.write_bytes(txt.replace("\\n","\\r\\n").encode())`
  with `assert txt.count(old) == 1`. Used heavily this session.
- **`python -c "..."` stdout is unreliable** under this shell (buffering /
  quoting). Write a `_scratch.py` and run it, or write output to a file and
  read it with the file tool. Do NOT trust an empty `python -c` result.
- **`*.html` is gitignored** (`.gitignore` has `*.html` with `!docs/*.html`
  carve-outs). Scratch `_*.html` files are refused by the create tool — use
  `_*.txt` for scratch HTML fragments and read them from the patch script.
- **`del a b c`** works; `;` chaining, `Remove-Item`, `head`/`tail`,
  `type`/`cat` do NOT reliably work. Slice in Python.
- **`.flake8` still broken** — lint via
  `python -m flake8 --isolated --max-line-length=100 --extend-ignore=E203,W503 <files>`.
- **black**: the repo was NOT formatted with default black (no
  `pyproject.toml`; existing code wraps ~79). When the user wants black-clean,
  running `python -m black <files>` reformats pre-existing lines too — that is
  an explicit, accepted choice (session 10 did it for the 3 run_source-era
  scripts). Otherwise match the surrounding ~79-char style.
- **mypy**: `python -m mypy --follow-imports=silent --explicit-package-bases`.
  The only pre-existing error is the PyYAML stub note in `run_source.py`
  (`_pipeline_name`) and `generate_pipeline_yaml.py` — leave it.
- **Live SIT runner CLI**: global flags first —
  `python -m scripts.e2e_lib.shaw_tranert_smoke --as-app_int bootstrap`.
- **Java is gitignored** (`config/templates/*.java`); read via Python
  `Path.read_text(..., errors="replace")`, dump regions to a file, read with
  the file tool.

---

## 5. THE DEFERRED ISSUES — how to fix them (priority order)

These are the open follow-ups. Each is grounded; none requires guessing.

### 5.1 The size==2 batch-dates cost-merge case (the one carried-forward #22 item)

**What:** The cost fix in §3b is exact for the **1-date** batch
(`getShawLoanMasterBatchDates()` returns one date → only `cost2_primary`
populated, this batch). The **2-date** case is NOT yet validated and the SQL
may be wrong.

**The semantic gap:** the Java `get32075SQLCost2()` does
`map.putAll(get32075Cost(primary))` **then**
`map.putAll(get32075Cost(secondary))`, so for an account present in **both**,
**secondary OVERWRITES primary** (last-write-wins per account). The SQL
`cost2_union` does `UNION ALL` of primary+secondary then
`MAX(UNPAID) + SUM(FEE)` GROUP BY account — which **combines** both instead of
overwriting. These differ only when an account appears in both the rank=2
(primary) and rank=1 (secondary) history windows.

**How to fix:**
1. Find/produce a SIT batch where the driver self-selects **2** distinct
   `SHAW_LOAN_MASTER` batch dates (`V_SHAW_TRANERT_LOAN_MASTER_BATCH_DATES`
   returns 2). Until such data exists, do NOT change the SQL — you can't
   validate it.
2. When you have it: replace the `cost2_union` UNION-ALL+GROUP-BY with a
   per-account **overwrite**: prefer the secondary (rank=1) row when present,
   else primary (rank=2). E.g. `FULL OUTER JOIN` primary/secondary on
   `ACCT_NUM` and `COALESCE(secondary.val, primary.val)`, or a
   `ROW_NUMBER()` window that ranks secondary above primary and keeps `rn=1`.
   Each side's own value is already `MAX(UNPAID)+SUM(FEE)` (the §3b fix).
3. Re-run the live reconcile against the 2-date batch; expect 0 violations.

**Where:** `cost2_union` CTE in BOTH
`00_bootstrap/030_expected_tables.sql` (structure) and
`10_load/020_refresh_expected.sql` (data). A `NOTE(issue #22)` comment marks
both spots. Keep them in lock-step.

**Authority:** `DAOOperations.get32075SQLCost2()` + `get32075Cost()` in
`config/templates/DAOOperations (2).java`. Do NOT guess the merge order — it
is `putAll(primary)` then `putAll(secondary)`.

### 5.2 The orphaned `020_expected_views.sql` is now INCONSISTENT (new this session)

**What:** `00_bootstrap/020_expected_views.sql` (the pre-shape-B `CREATE VIEW`
file, superseded by `030_expected_tables.sql`) still contains its own
`cost1_agg` / `cost2_union` with the **old buggy** `SUM(UNPAID + FEE)`
aggregation. The §3b fix was applied only to `030` + the refresh, not to this
orphan. app_int CANNOT execute this file (no `CREATE VIEW`), so it is dead in
production — BUT it is still covered by the parse-smoke test and now diverges
from the corrected logic, which is misleading to a reader.

**How to fix (decide with the user, session-9 §8.2 was "keep it"):**
- **Option A (recommended): delete `020_expected_views.sql` outright.** It is
  fully superseded by `030_expected_tables.sql`; app_int can't run it; deleting
  it removes the stale logic and one parse-smoke file. Confirm the
  parse-smoke test's file count expectation is updated.
- **Option B: keep it but apply the same `MAX(UNPAID)+SUM(FEE)` fix** so it
  doesn't mislead. Lower value (it's still non-executable) but preserves the
  "DBA reference" intent.

Do not leave it diverging silently into more sessions.

### 5.3 The four `regression_only` / `ignored_fields` (issue #18 §3 — long-deferred)

Not bugs; deliberate, user-approved deferrals. Each needs the Java caller's
**contact-iteration ordinal / primary flag** (stateful) or a missing source
column, so L2b cannot reproduce them and L3 covers them. Listed in the
reconciliation YAML's `ignored_fields`:

| Record | Field | Why deferred | To lift |
|---|---|---|---|
| 32005 | `CIF-REF-NUM-CUS` | needs per-account contact ordinal `cifRefNum` (stateful Java loop) | extend `V_SHAW_TRANERT_CONTACTS_MERGED` with a `CIF_REF_NUM` ordinal — only after confirming the Java caller's contact-iteration order against `DAOOperations`. Do NOT guess the order. |
| 32005 | `CIF-ACT-COD-CUS` (997/'P') | `cifRefNum == 997 && !primary -> 'P'` needs the same ordinal + primary flag | same as above + a `PRIMARY_REF_FLAG` ordinal |
| 32010 | `ST-COD-ORI` leg B | `DAOOperations.stateProvinceMap` is cross-run JVM state | not reproducible in SQL; keep on L3 |
| 32025 | `LN-OFC-CUR-COD` | `l.getMOfficer()` has no `M_OFFICER` column in `SHAW_LOAN_MASTER` (verified via `ALL_TAB_COLUMNS`) | needs a source column that does not exist; keep deferred |

Only the two 32005 fields are realistically liftable, and only after the
contact-iteration order is confirmed against the Java. The 32010 leg-B and
32025 officer fields are structurally impossible in SQL — leave them.

### 5.4 Carried-forward optional items (unchanged from session 9 §8.3)

- `.flake8` `chore:` fix (session-7 §8 #1).
- 3 stray `uzapp_ad0.V_SHAW_TRANERT_*` views (harmless; leave or clean).
- Dedicated least-privilege `valdo_harness_sit` user (replacing app_int).
- `LKP_VALDO_SHAW_*` → `LKP_VALDO_TRANERT_*` file-vs-source generalization
  ("6 files, >12 sources"): needs an ADR + a second source's TRANERT spec
  before promoting. Session-9 decision was option I (defer).

---

## 6. The four #22 acceptance criteria — status

| Criterion | Status |
|---|---|
| Known-good smoke: gate passes, no false positives | ✅ (0 violations live) |
| Known-bad smoke: one FailureRecord per drifted field | ✅ (1 mismatch → 1 record) |
| Two discrepancies fixed (Java cited) | ✅ |
| No `src/` changes | ✅ |
| pytest green; parse-smoke covers SQL | ✅ (264 e2e tests) |

Remaining open within #22: only §5.1 (size==2), flagged in-code and in the
issue note. #22 is effectively complete for the production (1-date) path.

---

## 7. Issue close-out state (decide with the user)

- **#18** (artifacts): scope met, merged. Can be closed.
- **#19** (wiring): all criteria met except the two E2E-smoke ones, which were
  explicitly rolled to #22 and are now satisfied. Can be closed.
- **#22** (format-align + discrepancies): both discrepancies fixed, gate green;
  only the size==2 sub-case remains (§5.1). Either close with a follow-up
  issue for §5.1, or keep #22 open scoped to §5.1.

The status notes on #19 and #22 already reflect this. No issues were closed
programmatically this session (stop-and-ask, §9).

---

## 8. Infographic tab (this session)

`docs/Valdo-Infographic.html` gained an "L2b SQL-Truth Gate" tab (Quality nav
group, 🧮). Wired into the sidebar nav, `TAB_LABELS` (`l2bgate`), and a
`<section id="panel-l2bgate">` between `panel-tests` and `panel-team`. Covers
the why/pipeline/parts/record-types/comparison-model/format-alignment, plus an
**"Adapting L2b to other file types"** section (single- vs multi-transaction,
a portable recipe, "what never changes"). 329 lines, additions only, CRLF
preserved. If you edit it, use the CRLF-safe Python patch approach (§4).

---

## 9. Stop-and-ask rules (carried forward, unchanged)

- Touching `stash@{0}` (still `On feature/issue-11-kill-file-search: wip ...`).
- Modifying anything under `src/` (AGENTS.md hard rule #1).
- Creating any lookup/expected content you would have to **invent** — the
  13-correction list on #17 and the Java in `config/templates/` are the only
  authorities. In particular do NOT guess the 32005 contact-iteration
  ordinal (§5.3) or the size==2 cost2 merge order (§5.1).
- Pushing / force-pushing / opening an MR.
- Creating or closing issues.
- Running anything destructive against SIT (DROP/DELETE/UPDATE). The
  whitelist refuses these.

---

## 10. Reading list for session 11 (priority order)

1. **This document.**
2. `docs/handover/L2B_SESSION_9_HANDOVER.md` — predecessor (expected-table set,
   the fan-out lesson, the CLI gotchas).
3. **AGENTS.md** — hard rules.
4. **Issues #18 / #19 / #22** + the **13-correction list on #17** — the
   authorities for any remaining row logic and the `regression_only` flags.
5. `config/e2e/sources/SHAW/reconciliation/tranert.yml` — the spec (note the
   two engine constraints in §2).
6. `config/e2e/sources/SHAW/sql/tranert/20_query/expected_*.sql` — the
   format-aligned wrappers.
7. `config/e2e/sources/SHAW/sql/tranert/10_load/020_refresh_expected.sql` +
   `00_bootstrap/030_expected_tables.sql` — the `cost1_agg`/`cost2_union`
   fix and the `NOTE(issue #22)` size==2 marker (§5.1).
8. `scripts/e2e_lib/run_source.py` — the `_run_l2b_sql_truth` gate wiring.
9. Java ground truth in `config/templates/` (gitignored; read via Python):
   `DAOOperations.get32075Cost()` / `get32075SQLCost2()` for §5.1.
