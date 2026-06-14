# Mapping Generator UI — Design

**Date:** 2026-03-01
**Status:** Approved

## Problem

Users (BAs, QA, developers) currently have no UI-based way to generate mapping JSON or validation rules JSON from an Excel/CSV template. They must use CLI commands, which blocks non-technical users.

The backend converters already exist:
- `TemplateConverter` → mapping JSON, exposed via `POST /api/v1/mappings/upload`
- `BARulesTemplateConverter` / `RulesTemplateConverter` → rules JSON, **no API endpoint yet**

## Solution

Add a **"Mapping Generator"** tab to the existing web UI (`src/reports/static/ui.html`) with two independent sections — one for field mappings, one for validation rules. Add one new backend endpoint for rules upload.

## UI Layout

```
[ Quick Test ] [ Recent Runs ] [ Mapping Generator ]

┌── Field Mapping ─────────────────────────────────┐
│  Drop mapping template (.xlsx or .csv)           │
│  ┌─────────────────────────────────────────────┐ │
│  │  📁 Drop here or click to browse            │ │
│  └─────────────────────────────────────────────┘ │
│  Mapping name: [______________]  Format: [auto▼] │
│  [Generate Mapping]                              │
│  ✅ 'p327_mapping' created  [Download JSON]      │
│                             [Use in Quick Test→] │
└──────────────────────────────────────────────────┘

┌── Validation Rules ──────────────────────────────┐
│  Drop rules template (.xlsx or .csv)             │
│  ┌─────────────────────────────────────────────┐ │
│  │  📁 Drop here or click to browse            │ │
│  └─────────────────────────────────────────────┘ │
│  Rules name: [___________]  Type: [BA-friendly▼] │
│  [Generate Rules]                                │
│  ✅ 'p327_rules' created  [Download JSON]        │
└──────────────────────────────────────────────────┘
```

## Components

### Frontend (`src/reports/static/ui.html`)

1. **New tab button** — `Mapping Generator` alongside Quick Test and Recent Runs
2. **`switchTab()`** — updated to include `'mapping'` in the tabs array
3. **Field Mapping section**
   - Drop zone (reuses existing drop zone pattern)
   - Mapping name text input (optional; defaults to filename stem)
   - Format dropdown: `Auto-detect`, `Fixed Width`, `Pipe-delimited`, `CSV`, `TSV`
   - Generate Mapping button (disabled until file selected)
   - Status area: spinner while uploading, then success message + Download JSON link + "Use in Quick Test →" button
4. **Validation Rules section**
   - Drop zone (same pattern)
   - Rules name text input (optional)
   - Rules type dropdown: `BA-friendly`, `Technical`
   - Generate Rules button (disabled until file selected)
   - Status area: spinner while uploading, then success message + Download JSON link

### "Use in Quick Test →" button

On success, switches to the Quick Test tab and calls `loadMappings()` to refresh the mapping dropdown so the newly generated mapping is immediately selectable.

### Backend (`src/api/routers/rules.py` — new file)

New endpoint: `POST /api/v1/rules/upload`

- **Accepts:** `file` (UploadFile), `rules_name` (optional query param), `rules_type` (optional: `ba_friendly` | `technical`, default `ba_friendly`)
- **Logic:** Routes to `BARulesTemplateConverter` or `RulesTemplateConverter` based on `rules_type`
- **Output:** Saves to `config/rules/<rules_id>.json`
- **Returns:** `{rules_id, filename, size, message}`

Register the new router in `src/api/main.py` at prefix `/api/v1/rules`.

## Data Flow

```
User drops mapping template
  → POST /api/v1/mappings/upload  (existing)
  → TemplateConverter.from_excel/from_csv()
  → saved to config/mappings/<id>.json
  → UI shows: ✅ created + Download + Use in Quick Test

User drops rules template
  → POST /api/v1/rules/upload  (new)
  → BARulesTemplateConverter or RulesTemplateConverter
  → saved to config/rules/<id>.json
  → UI shows: ✅ created + Download JSON
```

## Template Column Requirements

### Mapping template
Required: `Field Name`, `Data Type`
Optional: `Position`, `Length`, `Format`, `Required`, `Description`, `Default Value`, `Target Name`, `Valid Values`

### Rules template (BA-friendly)
Required: `Rule ID`, `Rule Name`, `Field`, `Rule Type`, `Severity`, `Expected / Values`, `Enabled`

### Rules template (Technical)
Required: `Rule ID`, `Rule Name`, `Description`, `Type`, `Severity`, `Operator`

## Error Handling

- Invalid file type → HTTP 400, shown as error status in UI
- Missing required columns → HTTP 500 with detail message shown in UI
- Network failure → "Could not connect to server" error in UI

## Out of Scope

- Rules preview / inline JSON editor (future)
- Multi-sheet Excel support (future)
- Editing an existing mapping/rules via UI (future)
