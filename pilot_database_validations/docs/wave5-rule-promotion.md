# Wave 5 Rule Promotion Governance (Opt-In)

## Purpose
Promote extracted rule candidates into managed rule packs with:
- explicit review states
- unresolved queue handling
- rule conflict checks
- traceability metadata

This flow is opt-in and does not change default Wave 4 behavior.

## Tool
`tools/rule_promotion.py`

### Inputs
- `--extraction`: extraction artifact (`rules-extracted.json` or parser/e2e artifact with `conversion.rulesExtraction`)
- `--rule-pack`: target managed rule pack JSON
- `--decisions` (optional): reviewer decisions payload
- `--require-unresolved-resolution/--no-require-unresolved-resolution` (default: required)

### Outputs
- `--output-report`: promotion report with decision, blocking reasons, candidate states, unresolved queue, conflicts
- `--output-pack` (optional): updated managed rule pack

## Review Decision Payload Example
```json
{
  "decisions": {
    "W4A_32005_ACCOUNT_ID_DEFAULT_AB12CD34": {
      "reviewState": "approved",
      "reviewer": "qa-owner",
      "reviewedAt": "2026-02-24",
      "notes": "Validated against BA scenario set"
    },
    "W4A_32005_REL_TYPE_CONDITIONAL_DD44EE11": "deferred"
  }
}
```

## Conflict Checks
- `DUPLICATE_EXISTING_RULE`: same `ruleId` and same behavior (safe skip)
- `RULE_ID_CONFLICT`: same `ruleId` but different behavior (blocking)

## Blocking Conditions
Promotion decision returns `BLOCKED` if any:
- unresolved queue present (default policy)
- candidate still in `in_review`
- `RULE_ID_CONFLICT` found

`READY` means all blockers are cleared and approved candidates can be merged into the managed rule pack.

## Traceability
Promoted rules are stamped with `traceability` metadata:
- promotion version
- review state/reviewer/review timestamp
- source location
- source artifact reference

## Example
```bash
python3 tools/rule_promotion.py \
  --extraction generated/trials/wave4/rules-extracted.json \
  --rule-pack templates/rules/rule-pack.template.json \
  --decisions examples/templates/rule-promotion-decisions.sample.json \
  --output-report generated/trials/wave5/promotion-report.json \
  --output-pack generated/trials/wave5/rule-pack.promoted.json
```
