# Wave 2 End-to-End Runner

Script: `tools/e2e_runner.py`

## Purpose
Run a full deterministic pipeline from BA/QA template inputs to generated artifacts:

1. Template input(s) (`mapping`, optional `rules`, optional `file-config`) -> canonical ingest JSON
2. Canonical ingest JSON -> generated `mapping.json` + `rules.json` + `conversion-report.json`
3. Deterministic validation execution (field/record/group/file scopes) -> `summary-report.json` + `detail-violations.csv` + `telemetry.json`

## CLI
```bash
python3 tools/e2e_runner.py \
  --input examples/templates/mapping-template.csv \
  --rules-input examples/templates/rules-template.csv \
  --file-config-input examples/templates/file-config-template.csv \
  --out-dir generated
```

## Outputs
Default output dir: `generated/`

- `template-ingest.json`
- `mapping.json`
- `rules.json`
- `conversion-report.json`
- `summary-report.json` (real quality counts from executed rules)
- `detail-violations.csv` (one row per emitted violation)
- `telemetry.json` (quality + per-rule fail counts)
- `promotion-evidence.json`

## Determinism
Determinism is enabled by default (`--deterministic`). This sets stable metadata so repeated runs with identical inputs produce byte-identical artifacts:

- fixed timestamp (`1970-01-01T00:00:00+00:00`) in conversion/telemetry artifacts
- stable `runId` derived from canonical ingest content + version + owner
- stable sorted JSON object keys in runner-authored artifacts

Disable deterministic mode if needed:
```bash
python3 tools/e2e_runner.py --no-deterministic ...
```

## Derivation Mode (Wave 3)
For BA/QA templates missing `transaction_code` and/or `source_field`, the runner supports deterministic derivation:

```bash
python3 tools/e2e_runner.py \
  --input /Users/buddy/Downloads/mapping-t.xlsx \
  --out-dir generated/trials/wave3 \
  --derive-missing \
  --derive-transaction-code-mode sheet_name \
  --derive-source-field-mode definition
```

When derivation is used:
- canonical output remains schema-valid and deterministic
- `conversion-report.json` captures structured `warnings[]` entries per derived value
- conversion status becomes `WARN` (instead of `SUCCESS`) when there are warnings and no errors

## Wave 4 Promotion Gates (opt-in)
Promotion gates are disabled by default to preserve backward compatibility.

Enable with:
```bash
python3 tools/e2e_runner.py ... --promotion-gate
```

When enabled:
- pre-gate status (`SUCCESS|WARN|FAILED`) is evaluated using configured policy knobs
- terminal runner status becomes `PASS|FAIL`
- reasons and observed metrics are emitted to `promotion-evidence.json`

Useful knobs:
- `--warn-acceptance-mode block|review-required|auto-accept`
- `--warn-acceptance-max-open <n>`
- `--warn-signoff-ba/--warn-signoff-qa`
- `--warn-waiver-age-days <days>`
- completeness knobs (`--min-template-completeness-*`) and observed completeness inputs

## Compatibility Notes
- Existing parser and contract generator behavior is preserved by default (derivation disabled unless explicitly enabled).
- Promotion gate enforcement is opt-in (`--promotion-gate` default false), preserving default status semantics.
- `tools/generate-contracts.py` now accepts optional overrides:
  - `--generated-at`
  - `--input-ref`
- No existing required arguments were changed.
