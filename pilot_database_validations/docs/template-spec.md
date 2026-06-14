# Template Specification (BA/QA Input)

## Purpose
BA/QA can provide mapping/rules in XLSX or CSV. Framework converts these templates into normalized JSON (`mapping.json`, `rules.json`).

## Supported Input Types
- `.xlsx` (preferred for multi-tab)
- `.csv` (single logical section per file)

## Policy Knobs (Feedback-Loop Controls)
These are explicit, review-time policy knobs. For **Wave 5.1 pilot stabilization**, thresholds are now locked and must not be relaxed without formal signoff.

### WARN Acceptance (Pilot-Locked)
- `warn_acceptance_mode`: `review-required`
  - `block`: any WARN fails template acceptance.
  - `review-required`: WARN can be accepted only with BA + QA signoff and rationale.
  - `auto-accept`: WARN allowed unless escalated.
- `warn_acceptance_max_open`: `20` (pilot ceiling)
- `warn_acceptance_expiry_days`: `7`

### BA/QA Minimum Template Completeness (Pilot-Locked)
- `min_template_completeness_ba`: `95%`
- `min_template_completeness_qa`: `95%`
- `min_template_completeness_required_columns`: `100%`

Completeness is measured as:
- required sections present (`Mapping Fields`, `Rules`, `File Config`)
- required columns present in each section
- required enum/value constraints valid
- row-level required fields populated for BA/QA-owned entries

### Default Derivation Modes (Pilot-Locked)
- `derivation_default_transaction_code`: `disabled`
- `derivation_default_source_field`: `disabled`
- `derivation_mode_on_enable`: `placeholder` (fallback only when derivation is explicitly enabled)

Allowed derivation modes when explicitly enabled:
- transaction code: `sheet_name | column_fallback | placeholder`
- source field: `target_field | definition | column_fallback | placeholder`

## Required Template Sections

## 1) Mapping Fields
Required columns:
- `transaction_code`
- `target_field`
- `source_field`
- `data_type` (string|numeric|date|boolean)
- `required` (Y|N|Conditional)
- `length`
- `position_start` (fixed-width only)
- `position_end` (fixed-width only)
- `format` (optional)
- `default_value` (optional)
- `transform_logic` (optional)

## 2) Rules
Required columns:
- `rule_id`
- `rule_name`
- `scope` (field|record|group|file)
- `severity` (ERROR|WARN|INFO)
- `priority` (integer)
- `expression`
- `message_template`
- `group_by` (required when scope=group)
- `enabled` (Y|N)

## 3) File Config
Required columns:
- `format` (fixed-width|delimited)
- `delimiter` (required if delimited)
- `quote_char` (optional)
- `escape_char` (optional)
- `record_length` (required if fixed-width)
- `header_enabled` (Y|N)
- `header_total_count_field` (optional)

## Header Alias Map (examples)
- `Column` -> `target_field`
- `Transformation` -> `transform_logic`
- `Position` -> `position_start`
- `Length` -> `length`
- `Required` -> `required`
- `Valid Values` -> `allowed_values`

## Validation Rules for Templates
- All required columns must be present
- Unknown mandatory enum values rejected
- Duplicate `rule_id` rejected
- `position_end >= position_start` when both present
- Group rule must include `group_by`
- Default behavior remains strict/non-deriving (missing required fields are errors unless derivation is explicitly enabled by policy)
- WARN-only outcomes follow the configured WARN acceptance policy knobs and require documented signoff when `review-required`

## Error Reporting Format
Every conversion error must include:
- input file name
- sheet/tab name (for xlsx)
- row number
- column name
- error code
- clear remediation hint

## Output Artifacts
- `generated/mapping.json`
- `generated/rules.json`
- `generated/run-config.json` (optional)
- `reports/template-validation/<timestamp>.md`
