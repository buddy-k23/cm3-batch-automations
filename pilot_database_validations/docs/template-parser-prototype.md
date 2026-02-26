# Template Parser Prototype

Prototype parser implementation: `tools/template_parser.py`

## Scope
- CSV-first template parsing for BA/QA mapping/rules/file-config files
- Optional XLSX support (multi-sheet)
- Canonical output aligned to `schemas/template-ingest.schema.json`
- Row-level error reporting with remediation hints

## CLI
```bash
python3 tools/template_parser.py \
  --input examples/templates/mapping-template.csv \
  --rules-input examples/templates/rules-template.csv \
  --file-config-input examples/templates/file-config-template.csv \
  --output generated/template-ingest.json
```

Derivation mode (Wave 3) for missing `transaction_code` and `source_field`:
```bash
python3 tools/template_parser.py \
  --input /Users/buddy/Downloads/mapping-t.xlsx \
  --output generated/template-ingest.mapping-t.json \
  --derive-missing \
  --derive-transaction-code-mode sheet_name \
  --derive-source-field-mode definition
```

XLSX mode:
```bash
python3 tools/template_parser.py \
  --input path/to/template.xlsx \
  --output generated/template-ingest.json
```

## Input Detection
- Rules section detected by required columns:
  `rule_id,scope,severity,priority,expression,message_template`
- Mapping section uses a stronger signature (at least two mapping-centric headers such as
  `target_field,data_type,source_field,position_start,length,transform_logic,transaction_code`).
  This prevents false negatives on real workbooks missing `transaction_code/source_field`.
- File config now requires `format` plus either file-config hint headers
  (`delimiter,record_length,header_enabled,header_total_count_field,quote_char,escape_char`)
  or actual format values constrained to `fixed-width|delimited`.
  This avoids false `file_config` classification for mapping sheets with field-level `format`.

## Validation Included
- Required values present for required columns
- Meaningful-row filtering (drops blank/trailing worksheet rows before validation)
- Integer coercion accepts integer-like floats (e.g., `1.0 -> 1`) for numeric integer fields
- Required-flag normalization (`C -> Conditional`, `N/A -> blank/omitted`, case/spacing tolerant)
- Expanded header alias normalization (e.g., `position -> position_start`, `valid_vales -> valid_values`)
- Enum validation (`data_type`, `scope`, `severity`, `format`, etc.)
- Duplicate `rule_id` detection
- `position_end >= position_start`
- `group_by` required when `scope=group`
- `delimiter` required for `delimited` format
- `record_length` required for `fixed-width` format
- Strict canonical schema gate: output must validate against `schemas/template-ingest.schema.json`
- Contract schema failures are surfaced as `CONTRACT_VALIDATION_FAILED` with JSON-path pointer in `column`
- Optional deterministic derivation for missing mapping fields:
  - `transaction_code`: `sheet_name|column_fallback|placeholder`
  - `source_field`: `target_field|definition|column_fallback|lineage_hardening|placeholder`
- Derivation activity is emitted as structured warnings via `parse_template_with_report()`.

## Tests
- `tests/test_template_parser.py`
  - CSV happy path
  - duplicate rule ID failure path
  - XLSX multi-sheet happy path
  - mapping-vs-file-config classification hardening for real-world `format` column usage
  - blank-row filtering + integer-like float coercion + required normalization behavior

Run tests:
```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

## Wave 2 Integration
For the full pipeline (template -> canonical ingest -> mapping/rules -> validation execution), see:
- `docs/wave2-e2e-runner.md`
- `tools/e2e_runner.py`
