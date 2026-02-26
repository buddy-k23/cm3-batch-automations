# convert-template-to-json (Design)

## Goal
Convert BA/QA XLSX/CSV templates into validated JSON contracts.

## CLI Contract (proposed)
```bash
convert-template-to-json \
  --input <path.xlsx|path.csv> \
  --out-dir generated/ \
  --alias-map config/header-aliases.json \
  --strict
```

## Pipeline
1. Parse input file(s)
2. Normalize headers using alias map
3. Build canonical model (`template-ingest.schema.json`)
4. Validate canonical model (`template-ingest.schema.json`)
5. Generate mapping/rules JSON
6. Validate generated JSON against contracts (`mapping`, `rules`, `report`, `telemetry`)
7. Emit conversion + error report

Validation runtime:
- Prefer `jsonschema` when available.
- Fallback to built-in strict validator in `tools/schema_validation.py` when dependency is absent.

## Error Classes
- `HEADER_MISSING`
- `ENUM_INVALID`
- `POSITION_INVALID`
- `RULE_SCOPE_INVALID`
- `RULE_DUPLICATE_ID`
- `GROUPBY_REQUIRED`
- `CONTRACT_VALIDATION_FAILED`

## Output Files
- `generated/mapping.json`
- `generated/rules.json`
- `generated/conversion-report.json` (prototype conversion summary)
- `generated/run-config.json` (optional)
- `reports/template-validation/<timestamp>.md`

## Non-functional
- Deterministic generation order
- Stable IDs for repeat conversions
- Full row/cell traceability in errors
