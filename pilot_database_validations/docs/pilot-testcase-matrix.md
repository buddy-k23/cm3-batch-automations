# Pilot Source Testcase Matrix (Positive / Negative / Edge)

## Scope
This matrix adds explicit template-level test data for all pilot sources from `generated/pilot_contracts/index.json`:

- `SHAW_SRC_ATOCTRAN`
- `SHAW_SRC_EAC`
- `SHAW_SRC_ESA`
- `SHAW_SRC_EST`
- `SRC_SRC_NAS_TRANERT_MAPPING_SH`
- `SHAW_SRC_P327`
- `SHAW_SRC_TRANERT`

Artifacts live under `examples/pilot_testcases/<source>/` with three paths per contract input type:

- Mapping: `mapping-positive.csv`, `mapping-negative.csv`, `mapping-edge.csv`
- Rules: `rules-positive.csv`, `rules-negative.csv`, `rules-edge.csv`
- File config: `file-config-positive.csv`, `file-config-negative.csv`, `file-config-edge.csv`

Governance path templates are in:
- `examples/pilot_testcases/governance/promotion-input-positive.json`
- `examples/pilot_testcases/governance/promotion-input-negative.json`
- `examples/pilot_testcases/governance/promotion-input-edge.json`

Expected outcomes summary is in:
- `examples/pilot_testcases/expected-outcomes.json`

## Coverage by Path

### 1) Mapping path coverage
- **Positive:** valid `transaction_code`, `target_field`, `source_field`, `data_type`, position bounds, optional transform logic.
- **Negative:** missing `source_field`, invalid `data_type` (`varchar`), invalid `required` (`Maybe`), `position_end < position_start`.
- **Edge:** missing `transaction_code` intended for derivation-mode validation (`--derive-missing`), max-length RAW record shape.

### 2) Rules path coverage
- **Positive:** valid file + record scope rules with valid enum values and priorities.
- **Negative:** duplicate `rule_id`, invalid severity (`CRITICAL`), invalid priority (`high`), group rule without `group_by`.
- **Edge:** high-priority boundary style values, INFO/WARN mix, disabled rule behavior.

### 3) File-config path coverage
- **Positive:** valid delimited setup with delimiter/header metadata.
- **Negative:** missing delimiter for delimited format + invalid `header_enabled` token.
- **Edge:** fixed-width config with `record_length` and header disabled.

### 4) Governance / promotion gate coverage
- **Positive:** WARN state with BA/QA signoff and waiver age present (review-required-compatible).
- **Negative:** threshold and completeness failures, no signoffs, waiver missing.
- **Edge:** clean SUCCESS baseline at policy thresholds.

## Test Matrix (applies to every pilot source)

| Scenario | Mapping | Rules | File Config | Governance | Expected Outcome |
|---|---|---|---|---|---|
| Positive | valid row(s) | valid rows | valid delimited config | signed WARN acceptance input | PASS |
| Negative | missing/invalid fields + bad positions | duplicate + enum + group_by violations | invalid delimiter/header flag | failing promotion input | FAIL |
| Edge | derivation-required txn code | INFO/WARN + disabled rule | fixed-width shape | threshold-boundary input | WARN or PASS (policy/config dependent) |

## Execution hints
- Template parser validation:
  - `python3 tools/template_parser.py --input <mapping.csv> --rules-input <rules.csv> --file-config-input <file-config.csv> --output /tmp/out.json`
- Derivation edge validation:
  - add `--derive-missing --derive-transaction-code-mode sheet_name`
- Promotion gate validation:
  - use `tools/promotion_gate.py` / `tools/e2e_runner.py --promotion-gate` with governance JSON as input fixture payload.

## Contract safety
- No schema or runtime contract changes were introduced.
- Additions are fixture/template artifacts and documentation only.
