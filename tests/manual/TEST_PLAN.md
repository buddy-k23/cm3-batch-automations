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
2. The seven `EXPECTED_*_TBL` tables created and seeded with one row
   each:

   ```bash
   sqlplus app_int/<pwd>@localhost:1521/FREEPDB1 \
     @tests/manual/sql/shaw_setup.sql
   ```

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

**Expected outcome.** Zero L2b violations — the single INSERT per
`EXPECTED_*_TBL` matches the first row of each record type in
`tranert_shaw_test_valid.txt` (BK_NUM_ERT=1, APP_ERT=200,
LN_NUM_ERT='LN0000000000000001', EFF_DAT_ERT=2026-06-01, TRN_COD_ERT
per the umbrella's discriminator codes).

Cardinality assertions
(`one_per_driver_row` / `many_per_driver_row` /
`zero_or_one_per_driver_row` from
`config/e2e/sources/SHAW/reconciliation/tranert.yml`) will pass for
the rows that exist in the EXPECTED tables and surface diff lines for
rows present in the fixture but not in the EXPECTED tables. Extend
the INSERTs in `shaw_setup.sql` to cover the full 18-row fixture if
you want absolute parity.

---

## Section 4 — Cleanup

Stop the server (if it was started with `--pid-file`):

```bash
kill $(cat /tmp/valdo-server.pid)
```

Optionally drop the Oracle test tables — uncomment the `DROP TABLE`
block at the bottom of `tests/manual/sql/shaw_setup.sql` and re-run
the script:

```bash
sqlplus app_int/<pwd>@localhost:1521/FREEPDB1 \
  @tests/manual/sql/shaw_setup.sql
```

The fixture files in `tests/manual/fixtures/` are regenerated on
demand by `scripts/build_shaw_test_files.py`, so they can be removed
without ceremony.
