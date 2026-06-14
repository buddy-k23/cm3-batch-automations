# DB Compare Tab — UI Design Spec

**Date:** 2026-04-01
**Status:** Approved by user
**Feature:** New "DB Compare" tab in the Valdo Web UI

---

## Overview

Add a fifth tab — **DB Compare** — to the Valdo web UI that exposes the existing `POST /api/v1/files/db-compare` endpoint through a visual interface. The tab supports two comparison directions:

- **DB → File**: Staging database is the source of truth; the file contains the transformed output. Verifies the transformation was applied correctly.
- **File → DB**: File is the source of truth; the database (target schema, potentially via JOIN queries) contains the loaded result. Verifies the load was successful.

The backend API already exists. This spec covers the UI and the minimum API extensions required to support it.

**Trust model:** This feature targets authenticated internal users only (API key required on all endpoints). No multi-tenant support. SQL input is trusted; implementers should add a SELECT-only guard at the service layer as a defence-in-depth measure.

---

## Layout

### Tab placement
A fifth tab labelled **"DB Compare"** is added after "API Tester". Uses the same `#tab-dbcompare` / `#panel-dbcompare` ID pattern. The `switchTab()` function in `ui.js` must be updated to include `'dbcompare'` in the tabs array.

### Top-level structure (inside `#panel-dbcompare`)

```
[ Direction Bar ]
[ Split Panel: DB (left) | File (right) ]
[ Run DB Compare button ]
[ Results Area ]
```

---

## Direction Bar

A centered row:

```
🗄️ Database   [⇄ swap]   📄 File
                           "DB is source · File is actual"
```

- **`#dbcSwapBtn`** — swaps the direction label and panel border highlights
- **`#dbcDirectionLabel`** — badge showing current direction:
  - `db-to-file`: "DB is source · File is actual"
  - `file-to-db`: "File is source · DB is actual"
- State: `let _dbcDirection = 'db-to-file'` — swap only updates labels/borders, not form fields

---

## Split Panel

`display: grid; grid-template-columns: 1fr 1fr; gap: 12px`

### DB Panel (`#dbcDbPanel`)

Border: accent when DB is source (`db-to-file`), secondary when DB is actual (`file-to-db`).

#### Connection summary chip (`#dbcConnChip`)
- Collapsed: `🔌 <host> · <schema>` with `▸ edit` affordance
- Click expands `#dbcConnForm` inline (no modal)
- Fields: DB Adapter (select: oracle/postgresql/sqlite), Host/DSN, Username, Password (masked), Schema
- On collapse: chip re-renders with current values
- **Storage**: host, user, schema, adapter stored in `sessionStorage`. Password is **never** stored.
- **HTTPS warning**: when the connection form is expanded, if `window.location.protocol !== 'https:'`, show a yellow inline warning: "⚠️ Connection credentials will be sent over an unencrypted connection."

#### Test Connection button (`#dbcTestConnBtn`)
- Label: "🔗 Test Connection"
- On click: button enters loading/disabled state (spinner) while request is in flight
- Calls `POST /api/v1/system/db-ping` with connection fields
- On response: shows ✅ Connected or ❌ `<error message>` inline below the form
- Requires API key header

#### SQL Editor (`#dbcSqlEditor`)
- Plain `<textarea>` with `resize: vertical` and monospace font — consistent with the rest of the UI (the API Tester body editor is also a `<textarea>`)
- Minimum height: 120px
- Placeholder for `db-to-file`: `SELECT column1, column2 FROM SCHEMA.TABLE`
- Placeholder for `file-to-db`: `SELECT t1.col, t2.col FROM TARGET.TABLE1 t1 JOIN TARGET.TABLE2 t2 ON t1.id = t2.fk_id`

#### Key columns (`#dbcKeyColumns`)
- Single text input: comma-separated column names for row matching
- Shared label: shown once in DB panel with an info note

### File Panel (`#dbcFilePanel`)

Border: inverse of DB panel (accent when File is source, secondary otherwise).

#### Drop zone (`#dbcFileInput`)
- Same `.drop-zone` component and drag-and-drop handler as Quick Test

#### Mapping select (`#dbcMappingSelect`)
- Same `<select>` populated from `GET /api/v1/mappings` as Quick Test

#### Info note
- Static: `ℹ️ Key columns are shared — set in the DB panel`

#### Options checkboxes
- `#dbcApplyTransforms` — "Apply transforms from mapping" (default: checked) → `apply_transforms` API param
- `#dbcDownloadCsv` — "Download diff as CSV" (default: checked) → triggers client-side CSV download after results render

---

## Run Button (`#dbcRunBtn`)

**Disabled until:** file selected AND mapping selected AND SQL non-empty AND Host/DSN non-empty.

`updateDbcRunBtn()` re-evaluates on every input change event.

On click:
1. Button enters loading state (spinner, disabled)
2. POST to `POST /api/v1/files/db-compare` with:
   - `file` — uploaded file blob
   - `query_or_table` — SQL textarea content
   - `mapping_id` — selected mapping
   - `key_columns` — text input value
   - `output_format` — always `"json"`
   - `apply_transforms` — from checkbox
   - `db_host`, `db_user`, `db_password`, `db_schema`, `db_adapter` — from connection form
3. On success → render results, auto-download CSV if checkbox checked
4. On error → show error state (see Error States below)
5. Button re-enables after response

---

## Results Area (`#dbcResults`)

Hidden on load. Shown after every run (persists until next run starts, then cleared).

### Status banner
- `workflow_status === 'success'` → green "✅ Compare complete"
- `workflow_status === 'failed'` → red "❌ DB extraction failed — check your query and connection"
- Any non-2xx HTTP response → red "❌ Server error: `<detail>`"
- 404 (mapping not found) → amber "⚠️ Mapping not found"

### Metric cards (6 tiles — direction-aware labels)

| Card | DB→File field | File→DB field | Green | Amber | Red |
|------|--------------|--------------|-------|-------|-----|
| Source Rows | `db_rows_extracted` | `total_rows_file2` | — | — | — (accent) |
| Actual Rows | `total_rows_file2` | `db_rows_extracted` | — | — | — (accent) |
| Matching | `matching_rows` | `matching_rows` | ✓ | — | — |
| Differences | `differences` | `differences` | — | ✓ | — |
| Only in Source | `only_in_file1` | `only_in_file2` | — | — | ✓ |
| Only in Actual | `only_in_file2` | `only_in_file1` | — | — | ✓ |

Labels "Source Rows" and "Actual Rows" update when direction is swapped.

### Download Diff CSV button (`#dbcDownloadDiffBtn`)
- Visible when `differences > 0` OR `only_in_file1 > 0` OR `only_in_file2 > 0`
- Hidden when all zero (clean compare)
- **Strategy: client-side CSV generation**. The initial `db-compare` response JSON contains `field_statistics` (per-field diff data). The client builds a CSV blob from this data and triggers a download via `URL.createObjectURL()`. No second server round-trip required.
- CSV columns: `row_number`, `key_columns`, `field_name`, `db_value`, `file_value`, `difference_type`
- If `#dbcDownloadCsv` checkbox is checked, download fires automatically when results render
- Button shows loading spinner while blob is being constructed (for large `field_statistics` payloads)
- If `field_statistics` is absent from the response, button shows "⚠️ Detailed diff unavailable" and is disabled

### `aria-live`
`#dbcResults` has `aria-live="polite"` so screen readers announce when results appear.

---

## Error States

| Scenario | Display |
|----------|---------|
| 404 mapping not found | Amber banner: "⚠️ Mapping '{id}' not found" |
| 500 DB extraction failed | Red banner: "❌ DB extraction failed — {detail}" |
| 500 other server error | Red banner: "❌ Server error — {detail}" |
| Network error / timeout | Red banner: "❌ Request failed — check server is running" |
| `workflow_status === 'failed'` | Red banner with query hint |
| No diff data in response | Results show metric cards only; CSV button disabled with note |

Results area clears and re-renders on each new run.

---

## API Changes Required

### 1. Extend `POST /api/v1/files/db-compare` (`src/api/routers/files.py`)
Add the following `Form` parameters (all optional — fall back to env vars when absent):
```python
apply_transforms: bool = Form(False)      # was silently missing — now wired through
db_host: str = Form(None)
db_user: str = Form(None)
db_password: str = Form(None)
db_schema: str = Form(None)
db_adapter: str = Form(None)              # must be validated: only 'oracle'|'postgresql'|'sqlite'
```
- If `db_adapter` is provided but not in the allowed set → return 400 "Invalid db_adapter"
- **Initial scope: Oracle only for connection override.** PostgreSQL and SQLite override support is deferred to a follow-up. For non-Oracle adapters, the endpoint uses the server env vars as today.
- Service layer (`compare_db_to_file`) receives an optional `connection_override: dict` parameter; when present and adapter is oracle, it builds a temporary `oracledb` connection using the override values instead of `get_engine()`.

### 2. New `POST /api/v1/system/db-ping` (`src/api/routers/system.py`)
```python
@router.post("/db-ping")
async def db_ping(
    db_host: str = Form(...),
    db_user: str = Form(...),
    db_password: str = Form(...),
    db_schema: str = Form(""),
    db_adapter: str = Form("oracle"),
    _key=Depends(require_api_key),
):
    """Test a DB connection with provided credentials."""
```
- Returns `{"ok": true}` or `{"ok": false, "error": "<message>"}`
- Requires API key
- Oracle-only in initial scope (same scoping as above)

---

## Files to Create / Modify

| File | Change |
|------|--------|
| `src/reports/static/ui.html` | Add `#tab-dbcompare` + `#panel-dbcompare` full HTML structure |
| `src/reports/static/ui.js` | `switchTab` array update; all DB Compare JS (direction swap, conn form, run handler, results renderer, client-side CSV) |
| `src/reports/static/ui.css` | `.dbc-panel` border variants, `.dbc-conn-chip` styles |
| `src/api/routers/files.py` | Extend `db-compare` with connection override + `apply_transforms` fields |
| `src/api/routers/system.py` | Add `POST /api/v1/system/db-ping` |
| `src/services/db_file_compare_service.py` | Accept optional `connection_override` dict; Oracle path only |
| `tests/unit/test_api_db_compare_ui.py` | Tests for extended endpoint and db-ping |
| `tests/e2e/test_e2e_db_compare.py` | E2E tests for the new tab |
| `docs/USAGE_AND_OPERATIONS_GUIDE.md` | Document DB Compare tab, valdo db-compare UI workflow |

---

## Acceptance Criteria

- [ ] DB Compare tab appears in tab bar and is keyboard-navigable (`switchTab` array updated)
- [ ] Direction swap updates labels and panel borders; form fields unchanged
- [ ] Connection chip collapses/expands; HTTPS warning shown on non-HTTPS origins
- [ ] Test Connection button enters loading state while in flight; shows ✅/❌ result
- [ ] SQL editor is a `<textarea>` with `resize: vertical`; placeholder updates with direction
- [ ] Run button disabled until file + mapping + SQL + host all provided
- [ ] `apply_transforms` checkbox wired to API param (was previously missing from router)
- [ ] Results: direction-aware metric card labels ("Source Rows" / "Actual Rows")
- [ ] Results: status banner distinguishes DB-extraction failure from compare-found-differences
- [ ] Diff CSV generated client-side from `field_statistics`; auto-downloads when checkbox checked
- [ ] CSV button hidden when compare is clean (zero mismatches)
- [ ] CSV button shows disabled state with note when `field_statistics` absent
- [ ] DB password never stored in localStorage or sessionStorage
- [ ] `db_adapter` form param validated server-side; invalid value returns 400
- [ ] `POST /api/v1/system/db-ping` requires API key; returns `{"ok": bool, "error": str}`
- [ ] Dark/light theme respected throughout
- [ ] `#dbcResults` has `aria-live="polite"`; all inputs have `<label>` associations
