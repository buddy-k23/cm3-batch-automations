# Wave 2 Trial Report - `mapping-t.xlsx`

- **Date/Time:** 2026-02-24 21:30-21:45 EST
- **Workbook:** `/Users/buddy/Downloads/mapping-t.xlsx` (read-only; not modified)
- **Objective:** Run current prototype flow on real workbook and document conversion readiness.

## 1) Prototype flow execution

### Command run
```bash
python3 tools/template_parser.py \
  --input /Users/buddy/Downloads/mapping-t.xlsx \
  --output generated/trials/wave2/template-ingest.mapping-t.json
```

### Result
- **Exit code:** `1` (validation failure)
- **Error artifact:** `generated/trials/wave2/parser.stderr.json`
- **Primary blocker:** workbook sheets were detected as `file_config` because of the `format` header, but column values are COBOL/picture field formats (`9(5)`, `MM/DD/CCYY`, `-Z(12).9(2)`), not file-level format enum (`fixed-width|delimited`).

---

## 2) Parsed coverage findings

Additional profiling artifact: `generated/trials/wave2/workbook-profile.json`

### Sheet-level coverage
- Sheets parsed: **4** (`Sheet1`..`Sheet4`)
- Prototype-detected section type: **file_config (4/4 sheets)**
- Raw parsed rows: **1160**
- Meaningful non-empty mapping-like rows: **210**
  - Sheet1: 49 meaningful rows (999 raw due trailing/blank worksheet rows)
  - Sheet2: 35
  - Sheet3: 57
  - Sheet4: 69

### Header shape found on all sheets
```text
transform_logic, target_field, definition, data_type, position_start,
format, length, required, valid_vales, notes
```

### Coverage vs expected canonical template
- Mapping required headers present: `target_field`, `data_type`
- Mapping required headers missing: `transaction_code`, `source_field`
- Rules headers: not present
- File config required header `format`: present (caused misclassification)

---

## 3) Conversion gaps (why full conversion failed)

1. **Section detection ambiguity (critical)**
   - Current detection marks any sheet with `format` as `file_config`.
   - Real mapping sheets use a field-level `format` column, not file-level format enum.

2. **Missing required mapping fields (critical)**
   - `transaction_code` absent on all 210 meaningful rows.
   - `source_field` absent on all 210 meaningful rows.

3. **Type coercion mismatch (high)**
   - Numeric-looking cells from XLSX are emitted as strings like `1.0`, `5.0`.
   - Current integer parser expects exact integer string and fails for decimal-string integers.

4. **Enum mismatch in real-world required flags (medium)**
   - `required` contains values outside prototype enum (`C`, `N/A`) in addition to `Y/N/Conditional`.

5. **Blank-row inflation from XLSX sheetData (medium)**
   - Sheet1 emits 999 rows, but only 49 are meaningfully populated.
   - Parser should skip rows where all canonical-relevant cells are empty.

---

## 4) Alias-map and normalization needs

## Needed header aliases
- `definition` -> **new canonical field** (e.g., `target_definition`) *(do not map to `source_field` without BA decision)*
- `valid_vales` -> `valid_values` (typo normalization)
- optional: `notes` -> `notes` (pass-through metadata)

## Needed value normalization
- `position_start`, `length`: accept `"N.0"` as integer `N`.
- `required`: normalize `C` -> `Conditional`; treat `N/A` as blank/optional or dedicated enum (decision needed).
- data type safety: ensure case-insensitive normalization remains (`Numeric/String/Date/Boolean` -> lower-case canonical enum).

## Section-classification improvements
- Prefer `mapping` when a sheet contains mapping-centric headers (`target_field`, `data_type`, `position_start`, `length`, `transform_logic`) even if `format` exists.
- Require stronger signature for `file_config` (e.g., `format` + at least one of `delimiter|record_length|header_enabled`).

---

## 5) Partial outputs produced

- `generated/trials/wave2/parser.stderr.json` - validation errors from prototype parser run
- `generated/trials/wave2/workbook-profile.json` - workbook profiling summary used for this report

> No canonical ingest JSON was produced by strict parser run due current validation gating.

---

## 6) Prioritized remediation plan

### P0 (must fix before Wave 2 full conversion)
1. **Detection fix:** update section-type classifier to avoid false `file_config` matches on mapping sheets.
2. **Row filtering:** ignore empty/trailing worksheet rows during XLSX ingestion.
3. **Numeric coercion fix:** parse `1.0`/`5.0` as integers for position/length fields.

### P1 (required for high-fidelity mapping conversion)
4. **Alias-map expansion:** add configurable alias file support (not hardcoded), including typo correction and metadata columns.
5. **Required-flag normalization:** map `C`/`N/A` variants and document behavior.

### P2 (needed for production-grade completeness)
6. **Source field strategy:** define how `source_field` is derived for this workbook type (new column, lookup mapping, or transformation extraction).
7. **Transaction code strategy:** infer from sheet/workbook metadata or introduce explicit template column.

---

## 7) Recommendation for next trial

After P0+P1 fixes, rerun with:
1. relaxed mode allowing partial canonical output (emit row-level warnings instead of hard fail),
2. explicit unresolved-field report for `source_field` and `transaction_code`,
3. downstream `generate-contracts.py` on partial canonical payload to validate end-to-end artifact generation with controlled gaps.
