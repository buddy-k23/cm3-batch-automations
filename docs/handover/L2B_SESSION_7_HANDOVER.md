# L2b SQL-Truth Gate — Session 7 Handover

**Generated:** 2026-06-01
**Predecessor:** [`docs/handover/L2B_SESSION_6_HANDOVER.md`](L2B_SESSION_6_HANDOVER.md) — read first; this doc is a *delta* on session 6.
**Resume prompt that drove this session:** [`prompts/L2B_SESSION_7_RESUME_PROMPT.md`](../../prompts/L2B_SESSION_7_RESUME_PROMPT.md)
**Branch at handover:** `feature/issue-18-shaw-tranert-sql` (head `cbf39c4`, **local-only, never pushed**).
**Issues this session touched:** #18 (the cross-schema open question is RESOLVED; helper layer materialized + validated live; cross-source registry added).

---

## 1. Headline outcome

The session-6 open question — *"can the harness create the helper objects in
`app_int`?"* — is **resolved**. The answer:

- **`app_int` cannot create VIEWS** (it has no `CREATE VIEW` / `CREATE ANY VIEW`
  system privilege; confirmed via `session_privs`). Every `CREATE VIEW` —
  even over app_int-owned tables — fails **ORA-01031**.
- **`app_int` CAN create TABLES** (`CREATE TABLE`, `SELECT ANY TABLE`,
  `UNLIMITED TABLESPACE`), including `CREATE TABLE ... AS SELECT` (CTAS)
  that reads cross-schema `uzapp_ad0.*` source tables.

So the harness was switched to **shape (B): materialize every helper result
set as a app_int-owned TABLE** (CTAS structure-only + TRUNCATE/INSERT refresh),
instead of views. **Proven end-to-end live against SIT:** all 10 helper tables
build and validate; the new cross-source registry table builds and seeds.

Two commits landed this session (both local-only):

| Commit | Title |
|---|---|
| `30be23d` | `fix(sql): materialize SHAW TRANERT helper result sets as app_int tables (shape B)` |
| `cbf39c4` | `feat(sql): add cross-source TRANERT source registry dimension` |

---

## 2. Why views can exist in app_int but app_int didn't make them

A confusing observation early in the session: 6 `app_int.V_SHAW_TRANERT_*`
**views** existed despite app_int lacking `CREATE VIEW`. Resolved via
`all_objects` timestamps + owners:

- The `app_int.*` views were created by **`uzapp_ad0`** (which has cross-schema
  DDL rights — the same power that lets it `CREATE TABLE` in app_int) in a
  prior session, planting views *into* the app_int schema.
- There are also 3 `uzapp_ad0.V_SHAW_TRANERT_*` views in the **uzapp_ad0**
  schema (uzapp_ad0 creating in its own schema). These are harmless, in a
  different schema, not in our path — **left in place** (loose end, §8).

Key takeaway for session 8: **everything the harness creates lives in
`app_int`** (Policy A, classifier-enforced). The only `uzapp_ad0` objects in
play are **source tables we read from**.

---

## 3. Decisions made in session 7 (do not re-litigate)

| # | Decision | Choice | Why |
|---|---|---|---|
| 1 | Cross-schema route (session-6 Options I/II/III) | **Option I + shape (B)**: connect as `app_int`, materialize helpers as **tables** (not views) | Only path that handles all 10 helpers incl. cross-schema reads; no DBA `CREATE VIEW` grant needed (that would take ~7 days) |
| 2 | app_int connection mechanism | **(b)** new audit-logged `--as-app_int` flag reading `ORACLE_*_SIT_APP_INT`, DSN falls back to `ORACLE_DSN_SIT` | Keeps the uzapp_ad0 path intact; lower blast radius than overwriting SIT vars |
| 3 | Helper-table shape | **CTAS structure-only (`WHERE 1=0`) + TRUNCATE/INSERT refresh**; no DROP | app_int + the whitelist forbid DROP; refresh keeps it idempotent and DROP-free |
| 4 | Helper-table naming | **Keep `V_SHAW_TRANERT_*` names** (now tables, not views) | Minimize SQL churn; the leading `V_` now just denotes "harness helper" |
| 5 | TRUNCATE gating | **Explicit `_TRUNCATE_ALLOWLIST` (option a)** + a drift test | Tighter than a prefix check; single reviewable list of what the harness may empty |
| 6 | Stale app_int views cleanup | **One-time manual `DROP VIEW`** (outside the harness) | Non-destructive (views hold no data); keeps the harness DROP-free |
| 7 | Lookup generalization scope | **Lookups now; helper-table rename deferred** | Helper rename is cosmetic until the upstream `SHAW_*` tables are unified (needs an ADR) |
| 8 | Source-registry keying | **`(LOCATION_CODE, ACTG_SYS_ID)` PK + `SOURCE_SYSTEM` + derived `CHARGE_OFF_STATUS`** | `ACTG_SYS_ID` (from FINANCIAL_EXTRACT) == `SYSTEM_ID_7000` (CDS_ACCT_EST view); single code column suffices |
| 9 | Registry data source | **`uzapp_ad0.FINANCIAL_EXTRACT`** (dedup-free), NOT `CDS_ACCT_EST` (has dupes) | User direction |
| 10 | Registry population | **Static, hand-maintained seed file** (NOT extractor-generated) | Slow-changing dimension; `SOURCE_SYSTEM` is operator-assigned |
| 11 | `*CO` codes | **Inherit `SOURCE_SYSTEM` from base** (RNCO→Ready Now) + `CHARGE_OFF_STATUS='MOVING_TO_CO'` | User rule: `*CO` = last delinquent day moving to charge-off |
| 12 | Invalid codes `ECO`/`DCO`/`TVCO` | **Excluded from the seed** | User: invalid |

---

## 4. What landed — commit `30be23d` (shape B)

**The `--as-app_int` mechanism** (`scripts/e2e_lib/shaw_tranert_smoke.py`):
- `_connect_sit(as_app_int=...)` reads `ORACLE_USER_SIT_APP_INT` /
  `ORACLE_PASSWORD_SIT_APP_INT`; DSN prefers `ORACLE_DSN_SIT_APP_INT`, falls
  back to `ORACLE_DSN_SIT` (same SIT host/service, different login).
- Threaded through `bootstrap`/`validate`/`query`/`discover`; emitted as a
  `connection_identity` audit event. Password never logged.

**Classifier changes (safety-critical):**
- Allow `CREATE TABLE ... AS SELECT` (CTAS) for harness prefixes; reported as
  kind `create_table_as_select`.
- Added `V_SHAW_TRANERT_` (helper) and `EXPECTED_` (per-record-type) prefixes
  to the allowed `CREATE TABLE` / `INSERT` prefixes.
- TRUNCATE now gated by explicit `_TRUNCATE_ALLOWLIST` + a drift test.

**SQL (new files):**
- `00_bootstrap/030_expected_tables.sql` — idempotent CTAS (ORA-00955 trap,
  `q'[ ]'` quoting) for the 10 helper tables; COST1 built before COST_MERGED.
- `10_load/020_refresh_expected.sql` — TRUNCATE + INSERT…SELECT refresh.

**Three live-surfaced SQL bugs fixed** (these bodies had never run on Oracle):
- **ORA-01723** — empty-string literals under CTAS → `CAST('' AS VARCHAR2(n))`
  (`CTM_CODE_DESC`, `FEE_CODE_KEY`).
- **ORA-00904** — `V_SHAW_TRANERT_CONTACTS_MERGED` referenced **invented**
  columns. Corrected from the Java ground truth (`DAOOperations.getTranertCus32005`
  result-set reads, lines ~3717-3721): `CIF_ACT_COD` (not `CIF_ACT_CODE`);
  `ECOA_CODE`/`CONS_INFO_IND` come from **`CBRS_TRW_SUMMARY` (a3)**, not
  `LOAN_CUST_INFO` (a2); dropped the invented `CIF_CSM_INF_IND_RAW`.
- **ORA-01790** — `UNION ALL` between the APPS leg (uzapp_ad0.*) and app_int
  leg had mismatched datatypes → explicit `CAST` on every column of **both**
  legs (the Java runs them as two separate queries and merges in code).

---

## 5. What landed — commit `cbf39c4` (cross-source registry)

New dimension table **`app_int.LKP_VALDO_TRANERT_SOURCE_REGISTRY`** — the first
object under the generalized `LKP_VALDO_TRANERT_` prefix (vs SHAW-specific
`LKP_VALDO_SHAW_`):

```
LOCATION_CODE     VARCHAR2(24)   -- 100020..100060
ACTG_SYS_ID       VARCHAR2(16)   -- = SYSTEM_ID_7000 in CDS_ACCT_EST view
SOURCE_SYSTEM     VARCHAR2(80)   -- operator-assigned
CHARGE_OFF_STATUS VARCHAR2(20)   -- CHARGED_OFF|WAREHOUSE|MOVING_TO_CO|NULL
PK (LOCATION_CODE, ACTG_SYS_ID)
```

- DDL: `00_bootstrap/040_source_registry.sql` (idempotent CREATE).
- Seed (18 rows): `10_load/030_refresh_source_registry.sql` (TRUNCATE + INSERT).
- Source of rows: distinct trimmed `(LOCATION_CODE, ACTG_SYS_ID)` in
  `uzapp_ad0.FINANCIAL_EXTRACT`. `ECO`/`DCO`/`TVCO` excluded → **18 rows**.
- `SOURCE_SYSTEM` mapping is the user's; `CHARGE_OFF_STATUS` derived
  (CAS→CHARGED_OFF, CASW→WAREHOUSE, *CO→MOVING_TO_CO, else NULL).
- Wiring: `LKP_VALDO_TRANERT_` added to allowed prefixes; registry added to
  `_TRUNCATE_ALLOWLIST`; drift test now also scans `040_source_registry.sql`;
  bootstrap order is now **6 files**.

**SHAW = `(LOCATION_CODE='100030', ACTG_SYS_ID/SYSTEM_ID_7000='LS')`** — the
only combo the Java pins for the SHAW TRANERT path (`location_code='100030'`,
`trim(system_id_7000)='LS'`). `SOURCE_SYSTEM='Shaw'` for that row.

---

## 6. Bootstrap order (current — 6 files)

```
1. 00_bootstrap/010_lookup_tables.sql        7 PL/SQL CREATE-TABLE blocks (LKP_VALDO_SHAW_*)
2. 00_bootstrap/040_source_registry.sql      1 PL/SQL CREATE-TABLE block  (LKP_VALDO_TRANERT_SOURCE_REGISTRY)
3. 10_load/010_load_lookups_from_csv.sql     168 TRUNCATE/INSERT ... VALUES  (auto-generated)
4. 10_load/030_refresh_source_registry.sql   1 TRUNCATE + 18 INSERT ... VALUES
5. 00_bootstrap/030_expected_tables.sql      10 PL/SQL CTAS blocks (V_SHAW_TRANERT_* helper tables)
6. 10_load/020_refresh_expected.sql          10 TRUNCATE + 10 INSERT ... SELECT
```

`run_validate` probes the 10 CTAS table names from `030_expected_tables.sql`.

The old `00_bootstrap/020_expected_views.sql` (CREATE VIEW) is **orphaned**
— no longer in the bootstrap order, **left in place for 5b** (it still holds
the canonical helper SQL bodies that 5b will compose). It is one of the two
unstaged `M` files in the working tree (see §9).

---

## 7. Live SIT verification (proof)

All as `app_int` via `--as-app_int`:

- `bootstrap` → **6 files, 225 statements, 0 failures, committed**
  (audit `reports/shaw_tranert_smoke/20260601T022335Z.jsonl`).
- `validate` → **10/10 helper tables OK**, incl. the 4 cross-schema ones
  (`CONTACTS_MERGED`, `BK1`, `BK3`, `STATE_PROVINCE`)
  (audit `reports/shaw_tranert_smoke/20260531T155837Z.jsonl`).
- Registry: owner `APP_INT`, 18 rows, correct `SOURCE_SYSTEM`/`CHARGE_OFF_STATUS`.
- Bootstrap is **idempotent** (re-ran clean a second time).

**Tests:** 83 passing in `tests/unit/test_e2e_shaw_tranert_smoke.py`.
**Gates:** black clean; mypy clean; flake8 clean **via `--isolated`** (see §8 loose end).

---

## 8. Loose ends (small, separate commits — your call on timing)

1. **`.flake8` is broken** — inline `#` comments in the `ignore` list make
   flake8 refuse to start (`ValueError: Error code '#' ... does not match`).
   Pre-existing, affects everyone. Fix as a `chore:` (move comments off the
   `ignore`/`exclude` value lines). Until then, lint via:
   `python -m flake8 --isolated --max-line-length=100 --extend-ignore=E203,W503 <files>`.
2. **3 stray `uzapp_ad0.V_SHAW_TRANERT_*` views** (different schema, harmless,
   not in our path). Leave or clean up — decide later.
3. **Long-term least-privilege** (dedicated `valdo_harness_sit` user) — still
   session 8+, not done. Session 7 uses `app_int` per the resume prompt.

---

## 9. Verify state before doing anything (session 8)

```powershell
git log --oneline -3
# Expect:
#   cbf39c4 feat(sql): add cross-source TRANERT source registry dimension
#   30be23d fix(sql): materialize SHAW TRANERT helper result sets as app_int tables (shape B)
#   0f02182 docs: add L2b session 6 handover and session 7 resume prompt

git status --short
# Expect exactly two unstaged M files (left for 5b):
#   M config/e2e/sources/SHAW/sql/tranert/00_bootstrap/020_expected_views.sql
#   M config/e2e/sources/SHAW/sql/tranert/20_query/cost2.sql
# Plus untracked leavings:
#   ?? "config/templates/DAOOperations (2).java"
#   ?? "config/templates/TranertMapper (1).java"
#   ?? prompts/L2B_Session_7_Continue_prompt.txt
#   (this handover will also appear once written)

git stash list
# Expect: stash@{0} unchanged from sessions 2-6

python -m pytest tests/unit/test_e2e_shaw_tranert_smoke.py --no-cov -q --tb=line
# Expect: 83 passed
```

If any of these are off, **STOP** and reconcile before proceeding.

### Windows shell gotchas (carried forward, re-confirmed session 7)

- **`read_file`/tools refuse gitignored paths.** Read `_ref/*` and
  `config/templates/*.java` via `run_command` + Python `read_text`.
- **PowerShell mangles multi-line `python -c "..."`** and chained commands
  with `;`/`>`/`echo`. Write a `_tmp.py` script file and run it, then read
  the result file with `read_file`. Single-line `-c` is OK.
- **Multi-paragraph commit messages:** write `_commit_msg_tmp.txt` via
  `create_file_with_contents`, then `git commit -F`, then delete.
- **`black` inserts CRLF on Windows;** `git add` LF→CRLF warnings are routine.
- **flake8:** use `--isolated` (broken `.flake8`, §8). **mypy:**
  `python -m mypy --follow-imports=silent --explicit-package-bases <file>`.
- **Clean up `_*.py` / `_*.txt` scratch files** before committing (several
  leaked this session because `del ... 2>$null` chains were swallowed).

---

## 10. Next plan (session 8) — in priority order

### 10.1 Commit 5b1 — simple `EXPECTED_*_TBL` tables (START HERE)

Materialize the straightforward per-record-type expected result sets as CTAS
tables composing the now-working helper tables:
`EXPECTED_BATCH_HEADER_TBL`, `EXPECTED_32000_TBL`, `EXPECTED_32040_TBL`,
`EXPECTED_32075_TBL`.

Per record type, reproduce the Java `TranertMapper.getTranertCus<NNNNN>`
per-row logic. **Authoritative sources (read first):**
- The **13-correction list comment on issue #17** (what each record type's
  expected output must produce).
- `config/templates/TranertMapper (1).java` (the `getTranertCus*` methods)
  and `config/templates/DAOOperations (2).java` (result-set field reads) —
  both gitignored; read via `run_command` + Python.
- `_ref/shaw-sql-statements.properties` (the SQL bodies; gitignored).

For each new `EXPECTED_*_TBL`:
- Add the CTAS to `030_expected_tables.sql` and the TRUNCATE+INSERT…SELECT to
  `020_refresh_expected.sql`.
- Add its `APP_INT.EXPECTED_*_TBL` name to `_TRUNCATE_ALLOWLIST` (the drift
  test enforces sync — the allow-list already has a NOTE placeholder for these).
- Validate live as app_int (`bootstrap` + `validate --as-app_int`).

**The classifier already accepts the `EXPECTED_` table prefix** — no code
change needed, only the allow-list entries.

### 10.2 Commit 5b2 — complex `EXPECTED_*_TBL` tables

The harder record types with CASE-cascade / bankruptcy / contact-merge logic:
`EXPECTED_32005_TBL`, `EXPECTED_32010_TBL`, `EXPECTED_32025_TBL`. These lean
on `CONTACTS_MERGED`, `BK1`/`BK3`, `STATE_PROVINCE`, `LOANS_NAME`. Same wiring
+ live validation.

> The user previously wanted 5b split into **5b1 (simple: 32000, 32040, 32075,
> header)** and **5b2 (complex: 32005, 32010, 32025)** — confirm the split
> still stands at the start of session 8.

### 10.3 Commit 6 — parse-smoke test

New `tests/unit/test_e2e_shaw_tranert_sql_smoke.py` that walks every `.sql`
under `config/e2e/sources/SHAW/sql/tranert/` and asserts:
- (offline) the bootstrap splitter (`sql_bootstrap._split_statements`) parses
  every file cleanly and every statement classifies as `is_safe`;
- (live, when SIT reachable) every helper/expected table body compiles —
  reuse the `validate` subcommand.

### 10.4 Optional / later

- Consider joining `LKP_VALDO_TRANERT_SOURCE_REGISTRY` into the helper tables
  (e.g. `DRIVER` could surface `SOURCE_SYSTEM`/`CHARGE_OFF_STATUS`) — **only
  if 5b needs it**; don't add speculatively.
- Generalize the 7 `LKP_VALDO_SHAW_*` property-lookups to `LKP_VALDO_TRANERT_*`
  + `SOURCE_SYSTEM`/`LOCATION_CODE` columns — **defer until a 2nd source
  arrives** (needs the extractor change + an ADR; scoped but not started).
- The `.flake8` `chore:` fix (§8).

---

## 11. Stop-and-ask rules (carried forward)

- Touching `stash@{0}`.
- Modifying anything under `src/` (AGENTS.md hard rule #1; the carve-out does
  not apply to #18).
- Creating any lookup/expected content you would have to **invent** — the
  13-correction list and the Java are the only authorities for 5b row logic.
- Force-pushing, or pushing the branch to origin.
- Running anything destructive against SIT (DROP/DELETE/UPDATE). The whitelist
  refuses these by design; the one-time view cleanup in §3.6 was a deliberate,
  manual, out-of-harness exception you approved.

---

## 12. Reading list for session 8 (priority order)

1. **This document** (`L2B_SESSION_7_HANDOVER.md`).
2. `docs/handover/L2B_SESSION_6_HANDOVER.md` — predecessor (schema topology,
   the original open question now resolved).
3. **AGENTS.md** — hard rules (#1 no `src/`, #3 no secrets, #6 stop at
   milestones).
4. **Issue #18** + the **13-correction list on #17** — authoritative for 5b
   expected-row logic.
5. `config/e2e/sources/SHAW/sql/tranert/00_bootstrap/030_expected_tables.sql`
   + `10_load/020_refresh_expected.sql` — the pattern 5b extends.
6. `config/e2e/sources/SHAW/sql/tranert/00_bootstrap/020_expected_views.sql`
   — orphaned but holds the canonical helper SQL bodies for reference.
7. `scripts/e2e_lib/shaw_tranert_smoke.py` — the classifier/allow-list/runner.
8. Java ground truth in `config/templates/` + `_ref/` (gitignored; read via
   Python).
