# Rules Extraction Catalog (Wave 4 / W4-A)

## Scope
Extraction source paths implemented:
1. Explicit **Rules** worksheet rows (`rule_id`, `scope`, `severity`, etc.) -> canonical `ruleRows`.
2. Mapping row `transform_logic` text -> **candidate normalized rule rows** (`conversion.rulesExtraction.extractedRuleRows`) when enabled.

## Supported transform normalization patterns

| Pattern class | Example input | Extracted outcome | Confidence |
|---|---|---|---|
| Default assignment | `Default to '001'`, `hardcode to 'USD'`, `default('00040')` | field rule with equality expression | high |
| Nullable / leave blank | `Leave Blank`, `Nullable --> Leave Blank` | field nullable rule (`== '' OR IS NULL`) | medium |
| Passthrough | `Pass as is`, `Transform as is` | field passthrough rule (`TARGET == SOURCE(field)`) | medium |
| Complex conditionals | `IF ... THEN ... ELSE ...` (multi-clause) | unresolved marker + warning | low |
| Unknown free text | long prose / external tab references | unresolved marker + warning | low |

## Unresolved handling
Unresolved rows are retained with:
- `unresolvedMarker = MANUAL_RULE_REQUIRED`
- normalized transform text
- source location (sheet + row)
- warning codes (`RULE_EXTRACTION_UNRESOLVED_IF_THEN`, `RULE_EXTRACTION_UNRECOGNIZED_TRANSFORM`)

This prevents silent drops and keeps review queues auditable.

## Determinism controls
- Stable extracted rule IDs: `W4A_<txn>_<target>_<kind>_<hash8>`
- Duplicate IDs are skipped with warning `RULE_EXTRACTION_DUPLICATE_RULE_ID`
- Output ordering is deterministic by `ruleId`
