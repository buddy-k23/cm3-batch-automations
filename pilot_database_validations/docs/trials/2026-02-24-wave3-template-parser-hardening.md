# Wave 3 Template Parser Hardening Report

- **Date/Time:** 2026-02-24 21:50-22:05 EST
- **Scope:** parser hardening for real-world workbook shapes (including `mapping-t.xlsx` findings)

## Implemented behavior changes

1. **Robust section detection**
   - Rules still require full rule signature.
   - Mapping detection now uses mapping-centric header signals (>=2), so sheets with a field-level `format` column are not misclassified as `file_config`.
   - File-config detection now requires `format` plus file-config context (`delimiter/record_length/header_enabled/...`) or explicit format values (`fixed-width|delimited`).

2. **Meaningful-row filtering**
   - CSV/XLSX readers now drop rows that are fully blank across normalized headers.
   - Prevents blank-row inflation from worksheet trailing rows.

3. **Numeric coercion**
   - Integer fields now accept integer-like float strings (`1.0`, `5.0`) and coerce to integer values.

4. **Required normalization policy**
   - `Y` -> `Y`
   - `N` -> `N`
   - `C`/`Conditional` -> `Conditional`
   - `N/A`/`NA`/`Not Applicable` -> blank (omitted from canonical payload)

5. **Header alias normalization expansion**
   - Improved normalization for spacing/punctuation/duplicate underscores.
   - Added aliases including `position -> position_start`, `valid_vales -> valid_values`, and related variants.

## Tests added/extended

- Extended `tests/test_template_parser.py` with:
  - mapping-like sheet with `format` column not misclassified as `file_config`
  - blank-row filtering + integer-like float coercion behavior
  - required normalization checks (`C`, `N/A`)
- Added fixture: `tests/fixtures/mapping_t_shape.csv`

## Validation run

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Result: **11 passed, 0 failed**.

## Decision notes

- Schema contracts were not changed.
- Parser still requires a `file_config` section (or separate file-config input) to produce canonical ingest output.
- For mapping sheets missing `transaction_code`/`source_field`, existing unresolved-fallback behavior remains unchanged (`UNRESOLVED_TXN`, `UNRESOLVED_SOURCE::<target>`).
