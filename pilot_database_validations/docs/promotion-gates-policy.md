# Promotion Gates Policy (Wave 4-C)

## Purpose
Convert pre-gate conversion statuses (`SUCCESS|WARN|FAILED`) into deterministic terminal promotion outcomes:
- `PASS`
- `FAIL`

`WARN` remains an intermediate signal and is resolved by policy when promotion gates are enabled.

## Defaults and Backward Compatibility
- Default runner behavior is unchanged: promotion gate enforcement is **disabled** unless explicitly enabled (`--promotion-gate`).
- When disabled, legacy status behavior (`SUCCESS|WARN|FAILED`) remains the run terminal status.
- A machine-readable evidence artifact is still emitted for auditability: `promotion-evidence.json`.

## Policy Knobs
These mirror governance knobs from `docs/template-spec.md` and `docs/stage-gates-checklist.md`:

- `warn_acceptance_mode`: `block | review-required | auto-accept` (default `review-required`)
- `warn_acceptance_max_open` (default `0`)
- `warn_acceptance_expiry_days` (default `7`)
- `min_template_completeness_ba` (default `95.0`)
- `min_template_completeness_qa` (default `95.0`)
- `min_template_completeness_required_columns` (default `100.0`)
- `derivation_default_transaction_code` (default `disabled`)
- `derivation_default_source_field` (default `disabled`)
- `derivation_mode_on_enable` (default `placeholder`)

## Blocking Precedence
1. Hard errors / pre-gate `FAILED`
2. Required-columns completeness below threshold
3. BA/QA completeness below thresholds
4. WARN count above threshold
5. WARN acceptance mode checks (`block`, or `review-required` without signoff/valid waiver)

## Evidence Artifact
Path: `generated/promotion-evidence.json` (or `<out-dir>/promotion-evidence.json`)

Contains:
- policy version and configured knobs
- observed run metrics (warn count, completeness, signoff metadata)
- deterministic evaluation reasons
- terminal decision (`PASS|FAIL`)

## CLI Usage
Enable promotion gates:

```bash
python3 tools/e2e_runner.py \
  --input examples/templates/mapping-template.csv \
  --rules-input examples/templates/rules-template.csv \
  --file-config-input examples/templates/file-config-template.csv \
  --out-dir generated \
  --promotion-gate
```

Review-required example for WARNs:

```bash
python3 tools/e2e_runner.py ... \
  --promotion-gate \
  --warn-acceptance-mode review-required \
  --warn-signoff-ba \
  --warn-signoff-qa \
  --warn-waiver-age-days 2
```
