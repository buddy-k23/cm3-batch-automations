# Valdo Engine v3 — Manual Test Plan

This document describes the manual smoke-test playbook for validating
Valdo end-to-end after the 5-sprint workbook-driven onboarding +
agentic MCP program. It exercises the five user-facing surfaces:

1. The `valdo onboard-source --check` CLI guardrail
2. The L1 structural validation engine (multi-record dispatch)
3. The Source Editor UI flow (workbook upload + drift detection)
4. The MCP server's 9-tool agent surface
5. (Optional) L2b SQL-truth reconciliation against Oracle

All artefacts referenced below live under `tests/manual/`. The fixed-
width fixtures are produced by `scripts/build_shaw_test_files.py` —
re-run it after any TRANERT mapping change and the files will track.

---

## Section 0 — Setup (seed the test database)

The L2b reconciliation flow (Section 3 / Scenario 6) compares parsed
fixture rows against materialized `EXPECTED_*_TBL` rows. Section 0
walks you through seeding those tables — plus the SHAW_* staging
tables — using the runner script
`tests/manual/seed_db.py`.

Two backends are supported:

| Backend | When to pick it                                            | Setup required               |
|---------|------------------------------------------------------------|------------------------------|
| SQLite  | Local dev, BA review, anywhere Oracle isn't reachable      | None — stdlib only            |
| Oracle  | Real reconciliation against Oracle XE / a shared environment | `oracledb` (already a dep) + `ORACLE_DSN` / `ORACLE_USER` / `ORACLE_PASSWORD` env vars |

### 0.1 — Generate the SQL seed files (if missing)

The two SQL artefacts (`shaw_setup.sql`, `shaw_setup_sqlite.sql`) are
produced by the same generator that builds the fixture files:

```bash
.venv311/bin/python scripts/build_shaw_test_files.py
```

Expected output:

```
TRANERT global record width: 636
  clean    -> 2 lines @ 636 chars each
  valid    -> 19 lines @ 636 chars each
  failures -> 5 lines (1 deliberately truncated)
  sql      -> shaw_setup.sql (~468 lines), shaw_setup_sqlite.sql (~438 lines)
  seed     -> EXPECTED INSERTs: BATCH_HEADER=1, NEW1=5, CUS=4, ORI=3, COD=2, CBRS=2, REC=2 (total 19)
  seed     -> SHAW_* staging: 6 tables x 5 rows = 30 synthetic rows
OK
```

### 0.2 — Apply the seed (SQLite — default, Oracle-free)

```bash
python tests/manual/seed_db.py --drop-first
```

This writes `tests/manual/valdo_test.db` (gitignored) and prints a
summary. Expected outcome (abridged):

```
Backend: sqlite

Seeded SQLite database: tests/manual/valdo_test.db
Tables present (13): ...

Row counts:
  EXPECTED_BATCH_HEADER_TBL         1  (OK)
  EXPECTED_NEW1_TBL                 5  (OK)
  EXPECTED_CUS_TBL                  4  (OK)
  EXPECTED_ORI_TBL                  3  (OK)
  EXPECTED_COD_TBL                  2  (OK)
  EXPECTED_CBRS_TBL                 2  (OK)
  EXPECTED_REC_TBL                  2  (OK)
  SHAW_COLLATERAL                   5  (OK)
  ... etc
```

All 13 rows must say `(OK)`. The detail-row counts (5/4/3/2/2/2) line
up exactly with the per-record-type row counts in
`tests/manual/fixtures/tranert_shaw_test_valid.txt`, so the L2b
comparator should report zero violations when fed the valid fixture.

### 0.3 — Apply the seed (Oracle — for the real run)

```bash
ORACLE_DSN=localhost:1521/FREEPDB1 \
ORACLE_USER=app_int \
ORACLE_PASSWORD=<pwd> \
    python tests/manual/seed_db.py --backend oracle --drop-first
```

`--drop-first` issues `DROP TABLE ... PURGE` for each SHAW_* /
EXPECTED_*_TBL before re-creating, so you can re-run between
iterations. If you prefer raw sqlplus:

```bash
sqlplus app_int/<pwd>@localhost:1521/FREEPDB1 \
    @tests/manual/sql/shaw_setup.sql
```

### 0.4 — Verify the seed

Spot-check tables / row counts directly:

```bash
# SQLite
sqlite3 tests/manual/valdo_test.db ".tables"
sqlite3 tests/manual/valdo_test.db "SELECT LN_NUM_ERT FROM EXPECTED_NEW1_TBL ORDER BY LN_NUM_ERT;"

# Oracle
sqlplus -S app_int/<pwd>@... \
    <<<"SELECT table_name FROM user_tables WHERE table_name LIKE 'SHAW_%' OR table_name LIKE 'EXPECTED_%' ORDER BY table_name;"
```

The `EXPECTED_NEW1_TBL.LN_NUM_ERT` query should return
`LN0000000000000001` .. `LN0000000000000005`, matching the NEW1 rows
in the valid fixture.

---

## Section 1 — Prerequisites

| Component       | Expected state                                                    |
|-----------------|-------------------------------------------------------------------|
| FastAPI server  | Running at `http://127.0.0.1:8001` with `VALDO_E2E_STUB=1`        |
| MCP transport   | Available at `http://127.0.0.1:8001/mcp/` (dev auth)              |
| UI              | Served at `http://127.0.0.1:8001/ui`                              |
| Admin login     | `e2e-user` / `e2e-password`                                       |
| MCP auth mode   | `VALDO_MCP_AUTH=dev` (no Authorization header required)           |
| Test fixtures   | Present in `tests/manual/fixtures/` (run the generator if absent) |
| Oracle (optional) | `tests/manual/sql/shaw_setup.sql` applied                       |
| Python venv     | `.venv311` (matches the rest of the repo)                         |

If the fixtures are missing, generate them:

```bash
.venv311/bin/python scripts/build_shaw_test_files.py
```

The generator prints the umbrella's global record width
(**636** chars for SHAW TRANERT — ORI's max position+length-1)
and produces three files:

* `tests/manual/fixtures/tranert_shaw_test_clean_no_violations.txt` (2 lines)
* `tests/manual/fixtures/tranert_shaw_test_valid.txt` (19 lines)
* `tests/manual/fixtures/tranert_shaw_test_structural_failures.txt` (5 lines, 1 deliberately truncated)

---

## Section 2 — Test scenarios

### Scenario 1 — Onboard-source dry-run (CI guardrail)

**What it tests.** The `--check` flag re-runs the EC-S3/S4/S5 emitters
in memory against the committed workbook and compares each artefact
to the on-disk state. A mismatch must surface as a drift line and exit
non-zero so CI can gate on it.

**How to run.**

```bash
.venv311/bin/valdo onboard-source templates/SHAW_onboarding.xlsx --check
```

**Expected outcome.**

```
onboard-source --check: SHAW
  drift /Users/<...>/config/rules/SHAW_TRANERT_CUS_rules.json: value for key 'rules' differs
  65 of 66 artefacts match.
```

Exit code: **1** (one drift expected — the CUS rules artefact, aka
"R028B" in the BA workbook).

---

### Scenario 2 — L1 structural validation (clean baseline)

**What it tests.** The multi-record dispatcher correctly identifies the
BATCH_HEADER (`position: "first"`) and the single NEW1 row by its
TRN-COD-ERT value (`32000`), and the header_trailer_count cross-type
rule passes because ITM-CNT-BRT=1 matches one detail row.

**How to run.**

```bash
.venv311/bin/valdo validate \
  -f tests/manual/fixtures/tranert_shaw_test_clean_no_violations.txt \
  --multi-record config/mappings/SHAW_TRANERT.yaml
```

**Expected outcome.**

* `Total Rows: 2`, `Error Count: 0` (no structural violations)
* The five `Expected at least 1 row of type 'rt_320XX'` cross-type
  warnings on the missing record types are **expected** for this
  minimal baseline — the umbrella declares `expect: at_least_one` for
  CUS, ORI, COD, CBRS, REC. They confirm the cardinality engine is
  wired up; they are not structural failures.
* Exit code: 1 (non-zero because of the missing-record-type
  expectations, but **zero hard structural violations**).

---

### Scenario 3 — L1 structural validation (deliberate failures)

**What it tests.** The structural-failure fixture is engineered to
trip four distinct gates:

| Line | Defect                                                | Gate            |
|------|-------------------------------------------------------|-----------------|
| 1    | `ITM-CNT-BRT=000000099` but only 4 detail rows        | `header_trailer_count` |
| 2    | NEW1 row truncated to 586 chars (50 short of 636)     | record-width    |
| 3    | NEW1 with `BK-NUM-ERT="ABCDE"` (non-numeric)          | field type      |
| 4    | NEW1-shaped row with unknown `TRN-COD-ERT=99999`      | dispatch        |
| 5    | Clean NEW1 row (control)                              | (none)          |

**How to run.**

```bash
.venv311/bin/valdo validate \
  -f tests/manual/fixtures/tranert_shaw_test_structural_failures.txt \
  --multi-record config/mappings/SHAW_TRANERT.yaml
```

**Expected outcome.** The CLI prints `✗ Multi-record validation failed`
and the cross-type violation list includes at minimum:

* `Unrecognized record type: discriminator value '99999' does not match any configured type.`
* Five `Expected at least 1 row of type 'rt_320XX'` lines (missing CUS / ORI / COD / CBRS / REC).

The truncated and bad-numeric rows surface inside the per-record-type
field violations (visible when an HTML report is generated with `-o
report.html`).

Exit code: 1.

---

### Scenario 4 — Source Editor UI flow

**What it tests.** End-to-end workbook → tree → drift → diff path
through the React-style Source Editor tab.

**How to run.**

1. Open `http://127.0.0.1:8001/ui` in a browser.
2. Log in as `e2e-user` / `e2e-password`.
3. Click the **Source Editor** tab.
4. Upload `templates/SHAW_onboarding.xlsx` via the file picker.
5. Wait for the tree to render.
6. Review the drift summary card at the top of the page.
7. Locate the `SHAW_TRANERT_CUS_rules.json` entry (the "R028B" drift)
   in the tree and click **View** to render the diff.

**Expected outcome.**

* Drift summary: **0 new / 1 changed / 65 unchanged**.
* The tree renders 66 artefacts grouped by kind
  (source YAML, mapping JSON/YAML, rules JSON, reconciliation YAML,
  expected SQL).
* The diff view for the changed entry renders without throwing in
  the browser console — matches the snapshot from the EF-S8
  agentic walkthrough.

---

### Scenario 5 — MCP smoke test (9-tool agent surface)

**What it tests.** The MCP HTTP transport handshake, the `tools/list`
inventory, and a representative read-only + onboarding tool call.

Dev mode (`VALDO_MCP_AUTH=dev`) means **no Authorization header is
needed**. Every request must include:

```
Content-Type: application/json
Accept: application/json,text/event-stream
```

**Step 5a — `initialize`.**

```bash
curl -sS -X POST http://127.0.0.1:8001/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json,text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
       "params":{"protocolVersion":"2024-11-05",
                 "capabilities":{},
                 "clientInfo":{"name":"valdo-test","version":"1"}}}'
```

Expected: `result.capabilities.tools` is present;
`result.serverInfo.name == "valdo"`.

**Step 5b — `tools/list`.**

```bash
curl -sS -X POST http://127.0.0.1:8001/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json,text/event-stream" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  | .venv311/bin/python -c \
      "import sys,json; r=json.loads(sys.stdin.read()); \
       print('tool_count:', len(r['result']['tools'])); \
       print('\n'.join(t['name'] for t in r['result']['tools']))"
```

Expected: `tool_count: 9`. The nine names are
`list_sources`, `get_source_spec`, `list_recent_runs`, `validate_file`,
`get_run_status`, `get_violations`, `upload_workbook_as_spec`,
`onboard_source_dry_run`, `infer_mapping_from_sample`.

**Step 5c — `tools/call list_sources`.**

```bash
curl -sS -X POST http://127.0.0.1:8001/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json,text/event-stream" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call",
       "params":{"name":"list_sources","arguments":{}}}'
```

Expected: `result.structuredContent.result` is an array containing
entries for **SHAW** and **SRC_A**.

**Step 5d — `tools/call onboard_source_dry_run`.**

```bash
curl -sS -X POST http://127.0.0.1:8001/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json,text/event-stream" \
  -d '{"jsonrpc":"2.0","id":4,"method":"tools/call",
       "params":{"name":"onboard_source_dry_run",
                 "arguments":{"workbook_path":"templates/SHAW_onboarding.xlsx"}}}'
```

Expected: `result.structuredContent.result.summary.total_files` is a
positive integer; `result.isError == false`; the response includes a
`would_write` array (no disk writes happened).

---

## Section 3 — Oracle-dependent tests (optional)

### Scenario 6 — L2b SQL-truth reconciliation against `EXPECTED_*_TBL`

**What it tests.** The L2b reconciliation engine compares parsed
output-file rows against materialized `EXPECTED_*_TBL` rows.

**Prerequisites.**

1. An Oracle XE instance reachable on the host (see
   `tests/manual/sql/shaw_setup.sql` header for connection options).
2. The seven `EXPECTED_*_TBL` tables plus the six SHAW_* staging tables
   created and seeded. Easiest path:

   ```bash
   ORACLE_DSN=localhost:1521/FREEPDB1 ORACLE_USER=app_int ORACLE_PASSWORD=<pwd> \
       python tests/manual/seed_db.py --backend oracle --drop-first
   ```

   Or for a quick local smoke run without Oracle (SQLite alternative):

   ```bash
   python tests/manual/seed_db.py --drop-first
   ```

   See Section 0 for full setup details and the expected row counts.
3. Valdo's `ORACLE_*` env vars pointing at the same instance.

**How to run.**

```bash
.venv311/bin/valdo run-etl-pipeline \
  --pipeline config/pipelines/SHAW.yml \
  --source SHAW \
  --file-type TRANERT \
  --output-file tests/manual/fixtures/tranert_shaw_test_valid.txt
```

(Or invoke the orchestrator's `db_truth_comparator` directly if
that's the path your environment uses.)

**Expected outcome.** Zero L2b violations — every detail row in
`tranert_shaw_test_valid.txt` has a matching `EXPECTED_*_TBL` row
seeded by `seed_db.py` (5 NEW1 + 4 CUS + 3 ORI + 2 COD + 2 CBRS + 2 REC
+ 1 BATCH_HEADER = 19 expected rows = 19 fixture rows). Each row's
key columns are identical:

* `BK_NUM_ERT = 1`
* `APP_ERT = 200`
* `LN_NUM_ERT = 'LN0000000000000001'` .. `'LN{seq:016d}'`
* `EFF_DAT_ERT = 2026-06-01`
* `TRN_COD_ERT = {32000, 32005, 32010, 32025, 32040, 32075}` per type

Cardinality assertions
(`one_per_driver_row` / `many_per_driver_row` /
`zero_or_one_per_driver_row` from
`config/e2e/sources/SHAW/reconciliation/tranert.yml`) pass cleanly for
the full 18-row fixture. If you ever extend the fixture (e.g. add a
sixth NEW1 row), re-run
`.venv311/bin/python scripts/build_shaw_test_files.py` — the SQL seed
files are regenerated atomically from the same row plan, so they stay
in lock-step.

---

## Section 4 — Cleanup

Stop the server (if it was started with `--pid-file`):

```bash
kill $(cat /tmp/valdo-server.pid)
```

Optionally drop the Oracle test tables — easiest path is the runner's
`--drop-first` flag, which fires `DROP TABLE ... PURGE` for each
SHAW_* / EXPECTED_*_TBL before re-creating:

```bash
ORACLE_DSN=... ORACLE_USER=... ORACLE_PASSWORD=... \
    python tests/manual/seed_db.py --backend oracle --drop-first
```

Or, manually, uncomment the `DROP TABLE` block at the bottom of
`tests/manual/sql/shaw_setup.sql` and re-run sqlplus.

For SQLite, simply delete the DB file:

```bash
rm -f tests/manual/valdo_test.db
```

The fixture files in `tests/manual/fixtures/` and the SQL artefacts in
`tests/manual/sql/` are regenerated on demand by
`scripts/build_shaw_test_files.py`, so they can all be removed
without ceremony.
