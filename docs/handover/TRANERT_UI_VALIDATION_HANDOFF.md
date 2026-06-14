# Session Handoff — SHAW TRANERT UI Validation Testing

**Generated:** 2026-06-12
**Trunk:** feature/valdo-engine-v3 (HEAD: f5a1f53)
**Predecessor context:** arch-review backlog complete (R-16 / #45 closed).
  Two DB Compare bugs fixed and pushed (f5a1f53).

---

## 1. What this session established

### DB Compare — working but wrong tool for TRANERT
- The DB Compare API (`POST /api/v1/files/db-compare`) was tested end-to-end
  against SIT Oracle (`APP_AUDIT.ERROR_DEFINITION`, 20 rows). Result: **passed**,
  20/20 rows matched. Two bugs were found and fixed:
  1. `db_file_compare_service._determine_workflow_status` — `if only_in_file1`
     raised *"truth value of DataFrame is ambiguous"* (non-chunked path returns
     DataFrames, not ints). Fixed with a `_count()` helper.
  2. `files.py` `db_compare` endpoint — `DbCompareResult` Pydantic model received
     raw DataFrames for `only_in_file1/2/differences`. Fixed with `_to_int()`.
- **DB Compare is NOT the right tool for TRANERT.** It works against a single
  flat table. TRANERT is a multi-record file with 7 record types — that requires
  either the Quick Test validate path or the E2E L2b gate.

### DB Compare tab is hidden in the UI
- `config/ui.yml` has `dbcompare: false`. The backend works; the tab is just
  disabled. To enable: change to `dbcompare: true` and restart the server.

### TRANERT file confirmed valid
- File: `C:\gitlab-ws\python\valdo\_ref\tranert_shaw_20260612.txt`
- 69 lines total. Discriminator at **position 170, length 5** (`TRN-COD-ERT`).
- Record type breakdown:

  | Record type | Discriminator | Count |
  |---|---|---|
  | `batch_header` | position="first" | 1 |
  | `rt_32000` (NEW1) | `32000` | 11 |
  | `rt_32005` (CUS)  | `32005` | 13 |
  | `rt_32010` (ORI)  | `32010` | 11 |
  | `rt_32025` (COD)  | `32025` | 11 |
  | `rt_32040` (CBRS) | `32040` | 11 |
  | `rt_32075` (REC)  | `32075` | 11 |
  | short/blank | — | 1 |

- All 6 expected record types present. Matches `SHAW_TRANERT.yaml` exactly.

---

## 2. What to do next session — TRANERT UI validation testing

### Goal
Validate `tranert_shaw_20260612.txt` through the Valdo UI (Quick Test tab)
using the `SHAW_TRANERT.yaml` umbrella config, and confirm:
- All record types parse correctly
- Business rules pass (or known violations are expected)
- The inline HTML report renders per-record-type results
- The `ITM-CNT-BRT` cross-type rule fires correctly

### Step-by-step

**Step 1 — Start the server**
```powershell
# From repo root (C:\gitlab-ws\python\valdo)
python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --reload
```
Then open `http://127.0.0.1:8000` in a browser.

**Step 2 — Quick Test → Validate**
1. Open the **Quick Test** tab (default landing tab).
2. Drag and drop (or browse to):
   `C:\gitlab-ws\python\valdo\_ref\tranert_shaw_20260612.txt`
3. In the **Multi-record YAML** section (below the mapping dropdown), click
   "Click to select a .yaml umbrella config" and choose:
   `config/mappings/SHAW_TRANERT.yaml`
   *(This overrides the mapping dropdown — you do NOT need to select a mapping.)*
4. Leave **Redact PII** checked.
5. Click **Validate**.

**Step 3 — Check the results**
Expected outcome (based on file content):
- `total_rows`: 68 (69 lines minus the blank/short line)
- `valid`: true (or known rule violations — see §3 below)
- Inline HTML report should show per-record-type breakdown
- Cross-type rule `ITM-CNT-BRT` check: batch header declares item count,
  verify it matches the 67 detail rows (11+13+11+11+11+11 = 68 — confirm
  what `ITM-CNT-BRT` actually says in the file)

**Step 4 — Also test via API Tester tab (optional)**
The API Tester tab can POST directly to `/api/v1/files/validate` with
`multi_record_config` as a file upload — useful for seeing the raw JSON
response alongside the HTML report.

---

## 3. Known expected rule behaviours to watch for

From the reconciliation YAML and ADR 0009:

| Record type | Field | Expected behaviour |
|---|---|---|
| `rt_32005` (CUS) | `CIF-REF-NUM-CUS` | Descending ordinal per account (998, 997, …). Rule R028B (`cross_row:sequential, start=998, step=-1`) should validate this. |
| `rt_32005` (CUS) | `CIF-ACT-COD-CUS` | Deferred — no rule covers the 997/'P' short-circuit. |
| `batch_header` | `ITM-CNT-BRT` | Must equal total detail row count. Cross-type rule fires as `error` if mismatch. |
| All detail types | `TRN-COD-ERT` | Must match the declared discriminator value for that record type. |

---

## 4. If validation fails unexpectedly

1. Check the inline HTML report — it shows which fields failed and on which rows.
2. Check `ITM-CNT-BRT` value in the batch header:
   ```python
   path = r'C:\gitlab-ws\python\valdo\_ref\tranert_shaw_20260612.txt'
   with open(path) as f: lines = f.readlines()
   # ITM-CNT-BRT: position 131, length 9 (from SHAW_TRANERT_BATCH_HEADER_mapping.json)
   print('ITM-CNT-BRT =', lines[0][130:139].strip())
   ```
3. If the server returns a 500 on the validate endpoint, check
   `_server_err.txt` in the repo root for the traceback.

---

## 5. Follow-up work already tracked

| Issue | Title |
|---|---|
| [#47](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/work_items/47) | Remove vestigial `MultiRecordValidator._extract_discriminator` / `_identify_record_type` methods |

---

## 6. Repo state at handoff

- **Branch:** `feature/valdo-engine-v3`
- **HEAD:** `f5a1f53` (pushed)
- **Working tree:** clean except known scratch files (`_*.py`) and gitignored leavings
- **Test baseline:** 30 failed / 2737 passed / 12 skipped (all pre-existing failures)
- **DB Compare tab:** disabled in `config/ui.yml` (`dbcompare: false`) — enable if needed
- **TRANERT file location:** `C:\gitlab-ws\python\valdo\_ref\tranert_shaw_20260612.txt`
  (not committed — local reference file only)
