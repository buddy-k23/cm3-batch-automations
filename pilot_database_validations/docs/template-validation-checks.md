# Template Validation Checks & Error Reporting (BA/QA Friendly)

## Purpose
This guide defines practical template checks and a human-readable error format for BA/QA users preparing CSV/XLSX files for conversion.

Aligned to:
- `docs/template-spec.md`
- `schemas/template-ingest.schema.json`
- `tools/convert-template-to-json-design.md`

## Validation Layers

### Layer 1: Structural Checks (before row-level validation)
1. **Required section/tab exists**
   - Mapping Fields, Rules, File Config
2. **Required headers present**
3. **Header alias normalization succeeds**
   - Example: `Column` -> `target_field`, `Transformation` -> `transform_logic`
4. **No duplicate normalized headers in same section**

### Layer 2: Field-Level Checks

#### Mapping Fields checks
- Required columns: `transaction_code`, `target_field`, `source_field`, `data_type`, `required`, `length`, `position_start`, `position_end`
- `data_type` in: `string|numeric|date|boolean`
- `required` in: `Y|N|Conditional`
- `length` positive integer when provided
- `position_start` / `position_end` positive integers when provided
- `position_end >= position_start` when both provided

#### Rules checks
- Required columns: `rule_id`, `rule_name`, `scope`, `severity`, `priority`, `expression`, `message_template`, `enabled`
- `scope` in: `field|record|group|file`
- `severity` in: `ERROR|WARN|INFO`
- `priority` integer >= 1
- `enabled` in: `Y|N`
- `rule_id` unique across rule rows
- if `scope=group`, `group_by` must be populated

#### File Config checks
- Required column: `format`
- `format` in: `fixed-width|delimited`
- if `format=delimited`, `delimiter` required (single character)
- if `format=fixed-width`, `record_length` required (positive integer)
- if `record_length` is provided for any format, it must be a positive integer
- `header_enabled` in: `Y|N` when provided

### Layer 3: Cross-Section Consistency Checks
- If file format is `fixed-width`, mapping rows should include usable `position_start` and `position_end`
- If file format is `delimited`, position columns may be optional but must still be valid integers if provided
- Rule references (where used in expressions) should target known fields from mapping (warning-level if deferred)

---

## Error Code Catalog (BA/QA View)

| Code | What it means | Typical fix |
|---|---|---|
| `HEADER_MISSING` | Required column/header is missing | Add the missing required column |
| `HEADER_DUPLICATE` | Same normalized header appears more than once | Keep one header; rename/remove duplicate |
| `ENUM_INVALID` | Value is not allowed for that column | Replace with one of the allowed values |
| `INTEGER_INVALID` | Value must be an integer | Enter a whole number |
| `INTEGER_RANGE_INVALID` | Integer is below allowed minimum | Use value in valid range (e.g., >= 1) |
| `POSITION_INVALID` | Fixed-width positions are inconsistent | Ensure `position_end >= position_start` |
| `RULE_DUPLICATE_ID` | Duplicate `rule_id` found | Make each `rule_id` unique |
| `GROUPBY_REQUIRED` | Group scope rule is missing `group_by` | Populate `group_by` for group rule |
| `DEPENDENCY_MISSING` | A conditional field requirement was not met | Fill in required supporting field |
| `CONTRACT_VALIDATION_FAILED` | Generated JSON did not pass schema contract | Fix upstream template errors and retry |

---

## Human-Readable Error Reporting Format

Use one line per issue in both machine-friendly and BA-friendly forms.

### Canonical fields per error
- `file`
- `sheet`
- `row`
- `column`
- `error_code`
- `message`
- `hint`

### Recommended JSON object shape
```json
{
  "file": "input-template.xlsx",
  "sheet": "Rules",
  "row": 8,
  "column": "group_by",
  "error_code": "GROUPBY_REQUIRED",
  "message": "group_by is required when scope='group'.",
  "hint": "Provide one or more grouping fields, e.g. account_id."
}
```

### Recommended markdown line format
```text
[input-template.xlsx | Rules | row 8 | group_by | GROUPBY_REQUIRED]
group_by is required when scope='group'.
Fix: Provide one or more grouping fields, e.g. account_id.
```

---

## Error Severity & Exit Guidance
- **ERROR**: Conversion must fail; outputs are not trusted.
- **WARN**: Conversion may proceed; review recommended.
- **INFO**: Informational only.

Recommended default for template ingestion: fail on first pass only after collecting all row-level **ERRORS** (batch feedback helps BA/QA fix in one cycle).
