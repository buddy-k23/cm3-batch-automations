# Contracts (Phase 1)

## Contract Set
- `schemas/mapping.schema.json`
- `schemas/rules.schema.json`
- `schemas/report.schema.json`
- `schemas/telemetry.schema.json`

## Validation Policy
1. Mapping and rule packs must validate before execution.
2. Unknown fields/operators are rejected.
3. Breaking schema changes require MAJOR version bump and approval.
4. Wave 2 parser/generator flow enforces strict JSON Schema checks for:
   - `template-ingest.schema.json`
   - `mapping.schema.json`
   - `rules.schema.json`
   - `report.schema.json`
   - `telemetry.schema.json`

## Validator Runtime Strategy
- Preferred engine: `jsonschema` (Draft 2020-12) when installed.
- Built-in fallback: `tools/schema_validation.py` strict validator subset (required/enum/type/additionalProperties/items/allOf/if-then/pattern/format/date-time).
- This keeps the flow dependency-light while preserving hard contract gates.

## Compatibility Policy
- Backward compatible additions: optional fields only.
- Incompatible changes: required-field additions, enum narrowing, semantic behavior change.

## Prototype Contract Generator (Canonical Ingest -> Contracts)
- Script: `tools/generate-contracts.py`
- Input: canonical ingest JSON conforming to `schemas/template-ingest.schema.json`
- Command:
  - `python3 tools/generate-contracts.py --input examples/canonical-ingest.sample.json --out-dir generated`
  - deterministic metadata overrides (optional):
    - `--generated-at 1970-01-01T00:00:00+00:00`
    - `--input-ref canonical-ingest.sample.json`
- Output artifacts:
  - `generated/mapping.json`
  - `generated/rules.json`
  - `generated/conversion-report.json`

## Example Artifacts to provide next
- `examples/mapping.simple.json`
- `examples/rules.simple.json`
- `examples/rules.account-sequencing.json`
- `examples/report.sample.json`
- `examples/telemetry.sample.json`
